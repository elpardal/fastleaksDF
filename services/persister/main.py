import asyncio
import structlog
from datetime import datetime
from sqlmodel import create_engine, Session, select
from aio_pika import connect_robust, ExchangeType
from shared.config import settings
from shared.models import IOCMatch, TelegramSource, Document, IOC, TelegramDocument

log = structlog.get_logger(service="persister")


class Persister:
    def __init__(self):
        self.engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
        self.connection = None
        self.channel = None

    async def connect_rabbitmq(self):
        self.connection = await connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=5)

        exchange = await self.channel.declare_exchange("fastleaksdf", ExchangeType.TOPIC, durable=True)
        queue = await self.channel.declare_queue("iocs.pending", durable=True)
        await queue.bind(exchange, routing_key="iocs.pending")
        return queue

    def _get_source_id(self, session: Session, tg_doc: TelegramDocument) -> int:
        stmt = select(TelegramSource).where(TelegramSource.doc_id == tg_doc.doc_id)
        source = session.exec(stmt).first()
        if not source:
            source = TelegramSource(
                doc_id=tg_doc.doc_id,
                chat_id=tg_doc.chat_id,
                message_id=tg_doc.message_id,
                filename=tg_doc.filename,
                mime_type=tg_doc.mime_type,
                size_bytes=tg_doc.size_bytes,
                channel_url=tg_doc.channel_url,
                timestamp=tg_doc.timestamp,
            )
            session.add(source)
            session.commit()
        return source.id

    def _get_document_id(self, session: Session, sha256: str, source_id: int, path: str, mime: str, size: int) -> int:
        stmt = select(Document).where(Document.sha256 == sha256)
        doc = session.exec(stmt).first()
        if not doc:
            doc = Document(
                sha256=sha256,
                storage_path=path,
                mime_type=mime,
                size_bytes=size,
                source_id=source_id,
            )
            session.add(doc)
            session.commit()
        return doc.id

    def _ioc_exists(self, session: Session, doc_id: int, ioc_type: str, value: str) -> bool:
        stmt = select(IOC).where(IOC.document_id == doc_id, IOC.ioc_type == ioc_type, IOC.value == value)
        return session.exec(stmt).first() is not None

    async def process_message(self, message):
        async with message.process():
            try:
                ioc_match = IOCMatch.model_validate_json(message.body)

                with Session(self.engine) as session:
                    # Busca documento para obter source_id
                    stmt = select(Document).where(Document.sha256 == ioc_match.file_sha256)
                    doc = session.exec(stmt).first()
                    if not doc:
                        log.warning("documento_nao_encontrado", sha256=ioc_match.file_sha256[:8])
                        return

                    # Deduplicação
                    if self._ioc_exists(session, doc.id, ioc_match.ioc_type, ioc_match.value):
                        log.debug("ioc_duplicado", sha256=ioc_match.file_sha256[:8], tipo=ioc_match.ioc_type)
                        return

                    # Persiste
                    ioc = IOC(
                        document_id=doc.id,
                        ioc_type=ioc_match.ioc_type,
                        value=ioc_match.value,
                        context=ioc_match.context,
                        line_number=ioc_match.line_number,
                        created_at=datetime.utcnow(),
                    )
                    session.add(ioc)
                    session.commit()

                    log.info(
                        "ioc_persistido",
                        ioc_id=ioc.id,
                        tipo=ioc.ioc_type,
                        documento=ioc_match.file_sha256[:8],
                        valor_preview=ioc.value[:32],
                    )

            except Exception as e:
                log.exception("erro_persistencia", error=str(e))

    async def start(self):
        queue = await self.connect_rabbitmq()
        log.info("persister_ativo", db_url=settings.database_url.split("@")[0] + "@...")
        await queue.consume(self.process_message)
        await asyncio.Event().wait()

    async def stop(self):
        if self.connection:
            await self.connection.close()
        log.info("persister_encerrado")


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    persister = Persister()
    try:
        await persister.start()
    except KeyboardInterrupt:
        await persister.stop()


if __name__ == "__main__":
    asyncio.run(main())
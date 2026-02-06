import asyncio
import tempfile
from pathlib import Path
import structlog
from telethon import TelegramClient
from aio_pika import connect_robust, ExchangeType, Message
from shared.config import settings
from shared.models import TelegramDocument, DownloadedFile
from shared.utils import compute_sha256, get_storage_path, is_extractable

log = structlog.get_logger(service="downloader")


class Downloader:
    def __init__(self):
        self.client = TelegramClient(
            f"{settings.telegram_session_name}_downloader",
            settings.telegram_api_id,
            settings.telegram_api_hash,
            flood_sleep_threshold=120,
        )
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect_telegram(self):
        await self.client.start()

    async def connect_rabbitmq(self):
        self.connection = await connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        self.exchange = await self.channel.declare_exchange(
            "fastleaksdf", ExchangeType.TOPIC, durable=True
        )

        queue = await self.channel.declare_queue("documents.pending", durable=True)
        await queue.bind(self.exchange, routing_key="documents.pending")
        await self.channel.declare_queue("documents.downloaded", durable=True)

        return queue

    async def download_document(self, tg_doc: TelegramDocument) -> Path:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(tg_doc.filename).suffix) as tmp:
            temp_path = Path(tmp.name)

        try:
            await self.client.download_media(
                await self.client.get_messages(tg_doc.chat_id, ids=tg_doc.message_id),
                file=temp_path,
            )
            return temp_path
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Download falhou: {e}") from e

    async def process_message(self, message):
        async with message.process():
            try:
                tg_doc = TelegramDocument.model_validate_json(message.body)
                temp_file = await self.download_document(tg_doc)

                sha256 = compute_sha256(temp_file)
                storage_path = get_storage_path(sha256, tg_doc.filename)
                storage_path.parent.mkdir(parents=True, exist_ok=True)
                temp_file.rename(storage_path)

                downloaded = DownloadedFile(
                    job_id=tg_doc.job_id,
                    doc_id=tg_doc.doc_id,
                    sha256=sha256,
                    storage_path=str(storage_path),
                    size_bytes=tg_doc.size_bytes,
                    mime_type=tg_doc.mime_type,
                    extractable=is_extractable(tg_doc.mime_type, tg_doc.filename),
                    original=tg_doc,
                )

                await self.exchange.publish(
                    Message(body=downloaded.model_dump_json().encode(), delivery_mode=2),
                    routing_key="documents.downloaded",
                )

                log.info(
                    "download_concluido",
                    sha256=sha256[:8],
                    filename=tg_doc.filename,
                    size_mb=round(tg_doc.size_bytes / 1024 / 1024, 2),
                    extractable=downloaded.extractable,
                )

            except Exception as e:
                log.exception("erro_download", doc_id=tg_doc.doc_id, error=str(e))

    async def start(self):
        await self.connect_telegram()
        queue = await self.connect_rabbitmq()
        log.info("downloader_ativo", prefetch=1)
        await queue.consume(self.process_message)
        await asyncio.Event().wait()

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()
        if self.connection:
            await self.connection.close()
        log.info("downloader_encerrado")


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    downloader = Downloader()
    try:
        await downloader.start()
    except KeyboardInterrupt:
        await downloader.stop()


if __name__ == "__main__":
    asyncio.run(main())
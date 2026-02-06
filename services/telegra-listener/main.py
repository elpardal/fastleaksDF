import asyncio
import structlog
from telethon import TelegramClient, events
from telethon.tl.types import Document
from aio_pika import connect_robust, Message, ExchangeType
from shared.config import settings
from shared.models import TelegramDocument

log = structlog.get_logger(service="telegram-listener")


class TelegramListener:
    def __init__(self):
        self.client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
            flood_sleep_threshold=120,
        )
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect_rabbitmq(self):
        self.connection = await connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        self.exchange = await self.channel.declare_exchange(
            "fastleaksdf", ExchangeType.TOPIC, durable=True
        )
        queue = await self.channel.declare_queue(
            "documents.pending",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "fastleaksdf-dlq",
                "x-dead-letter-routing-key": "documents.failed",
            },
        )
        await queue.bind(self.exchange, routing_key="documents.pending")

    async def on_new_document(self, event):
        if not event.message.document:
            return

        doc: Document = event.message.document
        if not getattr(doc, "size", 0) or not getattr(doc, "mime_type", ""):
            return

        filename = (
            next((attr.file_name for attr in doc.attributes if hasattr(attr, "file_name")), None)
            or f"doc_{doc.id}"
        )

        telegram_doc = TelegramDocument(
            doc_id=doc.id,
            chat_id=event.chat_id,
            message_id=event.message.id,
            filename=filename,
            mime_type=doc.mime_type,
            size_bytes=doc.size,
            channel_url=f"https://t.me/c/{str(abs(event.chat_id))[4:]}/{event.message.id}" if event.chat_id else None,
        )

        await self.exchange.publish(
            Message(body=telegram_doc.model_dump_json().encode(), delivery_mode=2),
            routing_key="documents.pending",
        )
        log.info("documento_capturado", doc_id=doc.id, filename=filename, size_mb=round(doc.size / 1024 / 1024, 2))

    async def start(self):
        await self.connect_rabbitmq()
        await self.client.start()
        log.info("conectado_telegram", channels=settings.channel_ids_list)

        for cid in settings.channel_ids_list:
            self.client.add_event_handler(
                self.on_new_document,
                events.NewMessage(chats=cid, func=lambda e: e.message.document),
            )
            log.info("monitorando_canal", channel_id=cid)

        log.info("listener_ativo")
        await asyncio.Event().wait()

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()
        if self.connection:
            await self.connection.close()
        log.info("listener_encerrado")


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    listener = TelegramListener()
    try:
        await listener.start()
    except KeyboardInterrupt:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(main())
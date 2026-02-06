import asyncio
import structlog
from pathlib import Path
from aio_pika import connect_robust, ExchangeType, Message
from shared.config import settings
from shared.models import DownloadedFile, ExtractedFile, IOCMatch
from shared.patterns import ioc_matcher

log = structlog.get_logger(service="scanner")


class Scanner:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect_rabbitmq(self):
        self.connection = await connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=2)

        self.exchange = await self.channel.declare_exchange(
            "fastleaksdf", ExchangeType.TOPIC, durable=True
        )

        q1 = await self.channel.declare_queue("documents.downloaded", durable=True)
        q2 = await self.channel.declare_queue("files.extracted", durable=True)
        await q1.bind(self.exchange, routing_key="documents.downloaded")
        await q2.bind(self.exchange, routing_key="files.extracted")
        await self.channel.declare_queue("iocs.pending", durable=True)

        return q1, q2

    def _should_scan(self, mime: str, filename: str) -> bool:
        text_ext = {".txt", ".csv", ".json", ".xml", ".log", ".ini", ".env", ".sql", ".conf", ".yml", ".yaml", ".md"}
        bin_ext = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mp3", ".exe", ".dll", ".so", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}
        ext = Path(filename).suffix.lower()
        return ext in text_ext or (ext not in bin_ext and any(t in mime.lower() for t in ["text", "json", "xml", "csv"]))

    async def scan_and_publish(self, file_path: str, sha256: str, job_id: str):
        if not Path(file_path).exists():
            return

        matches = ioc_matcher.scan_file(file_path, max_size_mb=10)
        for m in matches:
            ioc = IOCMatch(
                job_id=job_id,
                file_sha256=sha256,
                file_path=file_path,
                ioc_type=m["ioc_type"],
                value=m["value"],
                context=m["context"],
                line_number=m["line_number"],
            )
            await self.exchange.publish(
                Message(body=ioc.model_dump_json().encode(), delivery_mode=2),
                routing_key="iocs.pending",
            )

        if matches:
            log.info("iocs_encontrados", sha256=sha256[:8], count=len(matches), tipos=list(set(m["ioc_type"] for m in matches)))

    async def process_downloaded(self, message):
        async with message.process():
            try:
                d = DownloadedFile.model_validate_json(message.body)
                if self._should_scan(d.mime_type, Path(d.storage_path).name):
                    await self.scan_and_publish(d.storage_path, d.sha256, str(d.job_id))
            except Exception as e:
                log.exception("erro_scan", error=str(e))

    async def process_extracted(self, message):
        async with message.process():
            try:
                e = ExtractedFile.model_validate_json(message.body)
                if self._should_scan(e.mime_type, e.filename):
                    await self.scan_and_publish(e.storage_path, e.sha256, str(e.job_id))
            except Exception as e:
                log.exception("erro_scan", error=str(e))

    async def start(self):
        q1, q2 = await self.connect_rabbitmq()
        log.info("scanner_ativo", patterns=list(ioc_matcher.patterns.keys()))
        await q1.consume(self.process_downloaded)
        await q2.consume(self.process_extracted)
        await asyncio.Event().wait()

    async def stop(self):
        if self.connection:
            await self.connection.close()
        log.info("scanner_encerrado")


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    scanner = Scanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
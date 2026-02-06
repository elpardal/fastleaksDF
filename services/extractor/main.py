import asyncio
import zipfile
import rarfile
import tempfile
from pathlib import Path
import structlog
from aio_pika import connect_robust, ExchangeType, Message
from shared.config import settings
from shared.models import DownloadedFile, ExtractedFile
from shared.utils import compute_sha256, get_storage_path

log = structlog.get_logger(service="extractor")

MAX_EXTRACTED_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_RECURSION_DEPTH = 3
MAX_FILES_PER_ARCHIVE = 1000


class SafeExtractor:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect_rabbitmq(self):
        self.connection = await connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        self.exchange = await self.channel.declare_exchange(
            "fastleaksdf", ExchangeType.TOPIC, durable=True
        )

        queue = await self.channel.declare_queue("documents.downloaded", durable=True)
        await queue.bind(self.exchange, routing_key="documents.downloaded")
        await self.channel.declare_queue("files.extracted", durable=True)

        return queue

    def _is_safe_path(self, base: Path, target: Path) -> bool:
        try:
            return target.resolve().is_relative_to(base.resolve())
        except ValueError:
            return False

    async def _extract_zip(self, path: Path, output: Path) -> list[Path]:
        extracted = []
        total = 0
        count = 0

        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                count += 1
                if count > MAX_FILES_PER_ARCHIVE:
                    raise ValueError("Limite de arquivos excedido")

                if ".." in info.filename or info.filename.startswith(("/", "\\")):
                    log.warning("zip_traversal_bloqueado", filename=info.filename)
                    continue

                target = output / info.filename
                if not self._is_safe_path(output, target):
                    log.warning("caminho_inseguro", filename=info.filename)
                    continue

                total += info.file_size
                if total > MAX_EXTRACTED_SIZE:
                    raise ValueError("Limite de tamanho excedido")

                zf.extract(info, output)
                if not info.is_dir():
                    extracted.append(target)

        return extracted

    async def _extract_rar(self, path: Path, output: Path) -> list[Path]:
        extracted = []
        total = 0
        count = 0

        with rarfile.RarFile(path, "r") as rf:
            for info in rf.infolist():
                count += 1
                if count > MAX_FILES_PER_ARCHIVE:
                    raise ValueError("Limite de arquivos excedido")

                if ".." in info.filename or info.filename.startswith(("/", "\\")):
                    log.warning("rar_traversal_bloqueado", filename=info.filename)
                    continue

                target = output / info.filename
                if not self._is_safe_path(output, target):
                    log.warning("caminho_inseguro", filename=info.filename)
                    continue

                total += info.file_size
                if total > MAX_EXTRACTED_SIZE:
                    raise ValueError("Limite de tamanho excedido")

                rf.extract(info, output)
                if not info.isdir():
                    extracted.append(target)

        return extracted

    async def extract_recursive(self, downloaded: DownloadedFile, depth: int = 0) -> list[ExtractedFile]:
        if depth >= MAX_RECURSION_DEPTH:
            return []

        archive = Path(downloaded.storage_path)
        if not archive.exists():
            return []

        results = []
        with tempfile.TemporaryDirectory(prefix="fastleaks_extract_") as tmpdir:
            output = Path(tmpdir)

            try:
                if archive.suffix.lower() == ".zip":
                    files = await self._extract_zip(archive, output)
                elif archive.suffix.lower() == ".rar":
                    files = await self._extract_rar(archive, output)
                else:
                    return []

                for f in files:
                    sha256 = compute_sha256(f)
                    storage = get_storage_path(sha256, f.name)
                    storage.parent.mkdir(parents=True, exist_ok=True)
                    f.rename(storage)

                    ef = ExtractedFile(
                        job_id=downloaded.job_id,
                        parent_sha256=downloaded.sha256,
                        sha256=sha256,
                        storage_path=str(storage),
                        filename=f.name,
                        mime_type="application/octet-stream",
                        depth=depth + 1,
                    )
                    results.append(ef)

                    if f.suffix.lower() in (".zip", ".rar", ".7z"):
                        nested = await self.extract_recursive(
                            DownloadedFile(
                                job_id=downloaded.job_id,
                                doc_id=downloaded.doc_id,
                                sha256=sha256,
                                storage_path=str(storage),
                                size_bytes=storage.stat().st_size,
                                mime_type="application/octet-stream",
                                extractable=True,
                                original=downloaded.original,
                            ),
                            depth=depth + 1,
                        )
                        results.extend(nested)

            except Exception as e:
                log.exception("extracao_falhou", sha256=downloaded.sha256[:8], error=str(e))

        if results:
            log.info(
                "extracao_concluida",
                original=downloaded.sha256[:8],
                extraidos=len(results),
                profundidade=depth,
            )
        return results

    async def process_message(self, message):
        async with message.process():
            try:
                downloaded = DownloadedFile.model_validate_json(message.body)
                if not downloaded.extractable:
                    return

                extracted = await self.extract_recursive(downloaded, depth=0)
                for ef in extracted:
                    await self.exchange.publish(
                        Message(body=ef.model_dump_json().encode(), delivery_mode=2),
                        routing_key="files.extracted",
                    )
            except Exception as e:
                log.exception("erro_processamento", error=str(e))

    async def start(self):
        queue = await self.connect_rabbitmq()
        log.info(
            "extractor_ativo",
            max_size_mb=MAX_EXTRACTED_SIZE // 1024 // 1024,
            max_depth=MAX_RECURSION_DEPTH,
        )
        await queue.consume(self.process_message)
        await asyncio.Event().wait()

    async def stop(self):
        if self.connection:
            await self.connection.close()
        log.info("extractor_encerrado")


async def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    extractor = SafeExtractor()
    try:
        await extractor.start()
    except KeyboardInterrupt:
        await extractor.stop()


if __name__ == "__main__":
    asyncio.run(main())
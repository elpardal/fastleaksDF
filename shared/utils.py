import hashlib
import os
from pathlib import Path
from shared.config import settings


def compute_sha256(file_path: Path) -> str:
    """SHA256 com streaming (evita OOM)"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_storage_path(sha256: str, filename: str) -> Path:
    """Estrutura: storage/ab/cd/arquivo.zip"""
    storage = settings.storage_path
    prefix1, prefix2 = sha256[:2], sha256[2:4]
    path = storage / prefix1 / prefix2
    path.mkdir(parents=True, exist_ok=True)
    safe_name = f"{sha256}_{filename}"
    return path / safe_name


def is_extractable(mime_type: str, filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in (".zip", ".rar", ".7z") or any(t in mime_type.lower() for t in ["zip", "rar", "7z", "archive"])
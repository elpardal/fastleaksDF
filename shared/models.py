from datetime import datetime
from typing import Optional
from uuid import uuid4, UUID
from pydantic import BaseModel, Field, field_validator, computed_field
from sqlmodel import SQLModel, Field as SQLField


# ========== MENSAGENS RABBITMQ ==========

class TelegramDocument(BaseModel):
    """Documento capturado do Telegram → fila documents.pending"""
    job_id: UUID = Field(default_factory=uuid4)
    doc_id: int
    chat_id: int
    message_id: int
    filename: str
    mime_type: str
    size_bytes: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    channel_url: Optional[str] = None

    @field_validator("filename")
    @classmethod
    def sanitize_filename(cls, v: str) -> str:
        import re
        return re.sub(r"[^\w\.\-]", "_", v)[:255]


class DownloadedFile(BaseModel):
    """Arquivo baixado → fila documents.downloaded"""
    job_id: UUID
    doc_id: int
    sha256: str
    storage_path: str
    size_bytes: int
    mime_type: str
    extractable: bool
    original: TelegramDocument


class ExtractedFile(BaseModel):
    """Arquivo extraído → fila files.extracted"""
    job_id: UUID
    parent_sha256: str
    sha256: str
    storage_path: str
    filename: str
    mime_type: str
    depth: int = 0


class IOCMatch(BaseModel):
    """IOC encontrado → fila iocs.pending"""
    job_id: UUID
    file_sha256: str
    file_path: str
    ioc_type: str
    value: str
    context: str
    line_number: int


# ========== MODELOS POSTGRESQL ==========

class TelegramSource(SQLModel, table=True):
    __tablename__ = "telegram_sources"
    
    id: int | None = SQLField(default=None, primary_key=True)
    doc_id: int = SQLField(unique=True, index=True)
    chat_id: int
    message_id: int
    filename: str
    mime_type: str
    size_bytes: int
    channel_url: str | None = None
    timestamp: datetime = SQLField(default_factory=datetime.utcnow)


class Document(SQLModel, table=True):
    __tablename__ = "documents"
    
    id: int | None = SQLField(default=None, primary_key=True)
    sha256: str = SQLField(index=True, unique=True)
    storage_path: str
    mime_type: str
    size_bytes: int
    is_extracted: bool = False
    parent_id: int | None = SQLField(default=None, foreign_key="documents.id")
    source_id: int = SQLField(foreign_key="telegram_sources.id")


class IOC(SQLModel, table=True):
    __tablename__ = "iocs"
    
    id: int | None = SQLField(default=None, primary_key=True)
    document_id: int = SQLField(foreign_key="documents.id", index=True)
    ioc_type: str = SQLField(index=True)
    value: str = SQLField(index=True)
    context: str
    line_number: int
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
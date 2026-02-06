from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # RabbitMQ
    rabbitmq_url: str

    # Telegram
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str
    telegram_channel_ids: str

    # PostgreSQL
    database_url: str

    # Storage
    storage_path: Path = Path("./storage")

    # IOC Patterns
    ioc_patterns_cpf: str
    ioc_patterns_email: str
    ioc_patterns_domain: str
    ioc_patterns_ip_internal: str

    @property
    def channel_ids_list(self) -> List[int]:
        return [int(cid.strip()) for cid in self.telegram_channel_ids.split(",") if cid.strip()]


settings = Settings()
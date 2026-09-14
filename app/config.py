"""
Application configuration using Pydantic Settings.
All values are loaded from environment variables / .env file.
"""

import itertools
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ======================
    # Telegram Multi-Bot Pool
    # ======================
    bot_token: Optional[str] = Field(default=None, description="Primary Telegram Bot Token")
    bot_tokens: List[str] = Field(default_factory=list, description="List of worker bot tokens")
    admin_ids: List[int] = Field(default_factory=list)

    telegram_api_server: Optional[str] = Field(
        default=None,
        description="Local Bot API Server URL (e.g. http://telegram-bot-api:8081)",
    )
    telegram_api_id: int = Field(default=0)
    telegram_api_hash: str = Field(default="")

    # ======================
    # PostgreSQL
    # ======================
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_user: str = Field(default="vdbot")
    postgres_password: str = Field(default="change_me_strong_password")
    postgres_db: str = Field(default="video_downloader")

    # Optional full DSN override
    database_url: Optional[str] = Field(default=None)

    # ======================
    # Redis
    # ======================
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_password: Optional[str] = Field(default=None)

    celery_broker_url: str = Field(default="redis://localhost:6379/1")
    celery_result_backend: str = Field(default="redis://localhost:6379/2")

    # ======================
    # Bot Behaviour / Limits
    # ======================
    free_daily_limit: int = Field(default=5)
    free_max_quality: int = Field(default=720)

    rate_limit_requests: int = Field(default=10)
    rate_limit_window: int = Field(default=60)

    force_sub_channels: List[str] = Field(default_factory=list)

    # ======================
    # Downloader
    # ======================
    ytdlp_cookies_file: Optional[str] = Field(default=None)
    proxies: List[str] = Field(default_factory=list)
    download_dir: str = Field(default="/tmp/downloads")
    max_file_size_mb: int = Field(default=2000)

    # O'z-o'zida joylashtirilgan Cobalt (docker-compose'dagi "cobalt" xizmati).
    # Bo'sh bo'lsa faqat ochiq (public) instance ro'yxatiga tayaniladi.
    cobalt_local_url: Optional[str] = Field(default="http://cobalt:9000")
    cobalt_api_key: Optional[str] = Field(default=None)
    # Ochiq (public) Cobalt instance'lari ro'yxati (zaxira sifatida).
    # 2026 holatiga ko'ra deyarli barcha ochiq instance'lar endi
    # Turnstile orqali olinadigan JWT sessiya tokenini talab qiladi
    # ("error.api.auth.jwt.missing") — bu esa server-serverga oddiy
    # so'rov bilan olinishi mumkin emas, shuning uchun default bo'sh.
    # O'zingiz ishlaydigan (token talab qilmaydigan yoki API-key bergan)
    # instance topsangiz, shu yerga yoki .env'dagi COBALT_PUBLIC_INSTANCES
    # ga qo'shing.
    cobalt_public_instances: List[str] = Field(default_factory=list)

    # ======================
    # Logging / Environment
    # ======================
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")

    # ======================
    # Validators
    # ======================
    @field_validator("bot_tokens", mode="before")
    @classmethod
    def parse_bot_tokens(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("force_sub_channels", mode="before")
    @classmethod
    def parse_force_sub(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("proxies", mode="before")
    @classmethod
    def parse_proxies(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("cobalt_public_instances", mode="before")
    @classmethod
    def parse_cobalt_public_instances(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    # ======================
    # Computed properties
    # ======================
    @computed_field
    @property
    def all_bot_tokens(self) -> List[str]:
        """Barcha mavjud bot tokenlarini birlashtirib ro'yxat ko'rinishida qaytaradi."""
        tokens = list(self.bot_tokens)
        if self.bot_token and self.bot_token not in tokens:
            tokens.insert(0, self.bot_token)
        return tokens

    @computed_field
    @property
    def database_dsn(self) -> str:
        """Async SQLAlchemy DSN."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def redis_url(self) -> str:
        """Redis URL for general cache usage."""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (singleton)."""
    return Settings()


# Tokenlar rotatsiyasi uchun global cycle generator
_token_cycle = None


def get_next_bot_token() -> str:
    """
    Multi-Account Bot Pool uchun navbatdagi bot tokenini qaytaradi (Round-Robin).
    """
    global _token_cycle
    settings = get_settings()
    tokens = settings.all_bot_tokens

    if not tokens:
        raise ValueError("BOT_TOKEN yoki BOT_TOKENS bo'limida hech qanday token topilmadi!")

    if _token_cycle is None:
        _token_cycle = itertools.cycle(tokens)

    return next(_token_cycle)
# config.py

"""
Application configuration loaded from environment variables.

Uses `pydantic-settings` so that every config value is:
  - Typed and validated at startup.
  - Overridable via environment variables or a `.env` file.
  - Documented via field descriptions.

Usage:
    from config import get_settings
    settings = get_settings()
    print(settings.TARGET_REGION)
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralised, validated application settings.

    All values can be overridden by setting the corresponding
    environment variable (case-insensitive) or by placing them
    in a `.env` file at the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Trend provider ────────────────────────────────────────────────
    TARGET_REGION: str = Field(
        default="US",
        description="Default ISO 3166-1 alpha-2 country code for trend queries.",
    )

    TREND_PROVIDER: str = Field(
        default="google",
        description="Active trend provider: 'google' | 'youtube'.",
    )

    # ── Storage paths ─────────────────────────────────────────────────
    RAW_DATA_PATH: str = Field(
        default="data/raw",
        description="Filesystem directory for raw JSON output files.",
    )

    PROCESSED_DATA_PATH: str = Field(
        default="data/processed",
        description="Filesystem directory for processed JSON output files.",
    )

    # ── pytrends tunables ─────────────────────────────────────────────
    PYTRENDS_HL: str = Field(
        default="en-US",
        description="Host language passed to pytrends (e.g. 'en-US', 'id-ID').",
    )

    PYTRENDS_TZ: int = Field(
        default=360,
        description="Timezone offset in minutes from UTC for pytrends session.",
    )

    PYTRENDS_RETRIES: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retry attempts on transient pytrends errors.",
    )

    PYTRENDS_BACKOFF_FACTOR: float = Field(
        default=5.0,
        ge=0.5,
        description="Base backoff factor (seconds) for exponential retry delays.",
    )

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Python logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL.",
    )

    # ── Validators ────────────────────────────────────────────────────
    @field_validator("TARGET_REGION", mode="before")
    @classmethod
    def normalise_region(cls, value: str) -> str:
        """Ensure the region code is always uppercase."""
        return value.strip().upper()

    @field_validator("TREND_PROVIDER", mode="before")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        allowed = {"google", "youtube"}
        normalised = value.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"TREND_PROVIDER must be one of {allowed}, got '{value}'.")
        return normalised

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalised = value.strip().upper()
        if normalised not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}, got '{value}'.")
        return normalised


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached singleton `Settings` instance.

    The `lru_cache` ensures the `.env` file is parsed only once per
    process lifetime, which is both efficient and predictable.
    """
    return Settings()
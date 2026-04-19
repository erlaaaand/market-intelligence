# config.py

"""
Application configuration via environment variables and `.env` file.

Uses `pydantic-settings` v2 so every value is:
  - Strictly typed and validated at startup.
  - Overridable by environment variable (takes priority over .env).
  - Documented via field descriptions.

Usage:
    from config import get_settings
    settings = get_settings()
    print(settings.TARGET_REGION)

Testing:
    # Clear the LRU cache between test cases that need different settings:
    from config import get_settings
    get_settings.cache_clear()
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralised, validated application settings.

    Precedence (highest → lowest):
        1. Actual environment variables.
        2. Values in the `.env` file at the project root.
        3. Field defaults defined here.
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
    BRIEFS_DATA_PATH: str = Field(
        default="data/briefs",
        description=(
            "Filesystem directory for generated content brief JSON files. "
            "The Content Brief Generator writes individual brief files to "
            "<BRIEFS_DATA_PATH>/individual/ and batch summary files directly "
            "under <BRIEFS_DATA_PATH>/."
        ),
    )

    # ── pytrends tunables ─────────────────────────────────────────────
    PYTRENDS_HL: str = Field(
        default="en-US",
        description="Host language for pytrends (e.g. 'en-US', 'id-ID').",
    )
    PYTRENDS_TZ: int = Field(
        default=360,
        description="Timezone offset in minutes from UTC (420 = WIB / UTC+7).",
    )
    PYTRENDS_RETRIES: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts on transient pytrends errors.",
    )
    PYTRENDS_BACKOFF_FACTOR: float = Field(
        default=5.0,
        ge=0.5,
        description="Base back-off factor (seconds) for exponential retries.",
    )

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Python logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL.",
    )

    # ── Validators ────────────────────────────────────────────────────
    @field_validator("TARGET_REGION", mode="before")
    @classmethod
    def _normalise_region(cls, value: str) -> str:
        """Force the region code to uppercase and strip whitespace."""
        return str(value).strip().upper()

    @field_validator("TREND_PROVIDER", mode="before")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        allowed = {"google", "youtube"}
        normalised = str(value).strip().lower()
        if normalised not in allowed:
            raise ValueError(
                f"TREND_PROVIDER must be one of {sorted(allowed)}, got '{value}'."
            )
        return normalised

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalised = str(value).strip().upper()
        if normalised not in allowed:
            raise ValueError(
                f"LOG_LEVEL must be one of {sorted(allowed)}, got '{value}'."
            )
        return normalised


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached singleton `Settings` instance.

    The `lru_cache` ensures `.env` is parsed exactly once per process.
    Call `get_settings.cache_clear()` in tests to force re-loading.
    """
    return Settings()
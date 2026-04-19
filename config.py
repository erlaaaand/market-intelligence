# config.py

"""
Application configuration via environment variables and `.env` file.

Uses `pydantic-settings` v2 so every value is strictly typed, validated at
startup, and overridable by environment variable (which takes priority over
the .env file).

Usage:
    from config import get_settings
    settings = get_settings()
    print(settings.OLLAMA_MODEL)

Testing:
    from config import get_settings
    get_settings.cache_clear()   # force re-read between test cases
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
        description="Root directory for processed market-analysis reports. "
                    "Actual files land under <PROCESSED_DATA_PATH>/<REGION>/<DATE>/.",
    )
    BRIEFS_DATA_PATH: str = Field(
        default="data/briefs",
        description="Filesystem directory for generated content brief JSON files.",
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

    # ── LLM (Ollama) settings ─────────────────────────────────────────
    LLM_PROVIDER: str = Field(
        default="mock",
        description="LLM backend to use: 'ollama' | 'mock'. "
                    "Use 'mock' for offline development and testing.",
    )
    OLLAMA_BASE_URL: str = Field(
        default="http://localhost:11434",
        description="Base URL for the locally running Ollama server.",
    )
    OLLAMA_MODEL: str = Field(
        default="qwen3:30b",
        description="Ollama model tag to call (e.g. 'qwen3:30b', 'llama3:8b').",
    )
    OLLAMA_TIMEOUT: float = Field(
        default=120.0,
        ge=10.0,
        description="HTTP timeout in seconds for Ollama /api/chat calls.",
    )
    OLLAMA_RETRIES: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Number of parse/validation retry attempts for Ollama calls.",
    )
    LLM_TOP_N: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of raw trending records sent to the LLM.",
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

    @field_validator("LLM_PROVIDER", mode="before")
    @classmethod
    def _validate_llm_provider(cls, value: str) -> str:
        allowed = {"ollama", "mock"}
        normalised = str(value).strip().lower()
        if normalised not in allowed:
            raise ValueError(
                f"LLM_PROVIDER must be one of {sorted(allowed)}, got '{value}'."
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
    Return a cached singleton ``Settings`` instance.

    The ``lru_cache`` ensures ``.env`` is parsed exactly once per process.
    Call ``get_settings.cache_clear()`` in tests to force re-loading.
    """
    return Settings()
# main_brief.py

"""
Composition root for the Content Brief Generator.

This is the ONLY module that imports from `src.infrastructure`.
All concrete adapters are instantiated here and injected into the
use-case, preserving strict Dependency Inversion across all layers.

Entry points:
    python main_brief.py                           # interactive mode
    python main_brief.py --region ID               # filter by region
    python main_brief.py --file processed_*.json   # direct file mode
"""
from __future__ import annotations

import logging
import sys

from config import get_settings
from src.application.content_brief_generator import ContentBriefGeneratorUseCase
from src.infrastructure.json_brief_storage import JsonBriefStorageAdapter
from src.infrastructure.local_trend_reader import LocalTrendReaderAdapter
from src.infrastructure.rule_based_brief_generator import RuleBasedBriefGeneratorAdapter
from src.interfaces.brief_cli import run_brief_cli


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def main() -> None:
    """
    Wire and launch the Content Brief Generator pipeline.

    Execution order:
        1. Load and validate settings from .env / environment.
        2. Configure root-level logging.
        3. Instantiate LocalTrendReaderAdapter   (reads data/processed/).
        4. Instantiate RuleBasedBriefGeneratorAdapter (pure rule engine).
        5. Instantiate JsonBriefStorageAdapter   (writes data/briefs/).
        6. Assemble ContentBriefGeneratorUseCase via constructor injection.
        7. Hand control to the CLI layer.
    """
    # ── 1. Settings ───────────────────────────────────────────────────
    settings = get_settings()

    # ── 2. Logging ────────────────────────────────────────────────────
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.info("Content Brief Generator starting.")

    # ── 3. Trend reader ───────────────────────────────────────────────
    trend_reader = LocalTrendReaderAdapter(
        processed_base_path=settings.PROCESSED_DATA_PATH,
    )

    # ── 4. Brief generator ────────────────────────────────────────────
    brief_generator = RuleBasedBriefGeneratorAdapter()

    # ── 5. Brief storage ──────────────────────────────────────────────
    brief_storage = JsonBriefStorageAdapter(
        base_path=settings.BRIEFS_DATA_PATH,
    )

    # ── 6. Use-case assembly ──────────────────────────────────────────
    use_case = ContentBriefGeneratorUseCase(
        trend_reader=trend_reader,
        brief_generator=brief_generator,
        brief_storage=brief_storage,
    )

    # ── 7. CLI handoff ────────────────────────────────────────────────
    run_brief_cli(
        use_case=use_case,
        available_files_fn=trend_reader.list_available_files,
    )


if __name__ == "__main__":
    main()
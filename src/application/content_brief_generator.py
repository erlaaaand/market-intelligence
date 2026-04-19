# src/application/content_brief_generator.py

"""
Application use case: ContentBriefGeneratorUseCase.

Orchestrates the full Content Brief Generation pipeline. This module depends
exclusively on abstract ports defined in `src.core.brief_ports` and
`src.core.exceptions` — never on concrete infrastructure classes.

Pipeline:
    1. Resolve source: load a specific trend file or auto-select the latest.
    2. Guard: return an empty BriefBatch if the file contains no topics.
    3. Generate a ContentBrief for each TrendTopic via BriefGeneratorPort.
    4. Persist each brief individually via BriefStoragePort.save_brief().
    5. Bundle all briefs into a BriefBatch and persist via save_batch().
    6. Return the completed BriefBatch to the caller.
"""
from __future__ import annotations

import logging

from src.core.brief_entities import BriefBatch, ContentBrief
from src.core.brief_ports import BriefGeneratorPort, BriefStoragePort, TrendReaderPort
from src.core.entities import TrendTopic

logger = logging.getLogger(__name__)

# Segment of the processed filename that precedes the region code.
# Expected format: processed_trends_{REGION}_{TIMESTAMP}.json
_FILENAME_REGION_SEGMENT_INDEX = 2


class ContentBriefGeneratorUseCase:
    """
    Orchestrates the end-to-end Content Brief Generation pipeline.

    All three dependencies are injected at construction time, making this
    class fully unit-testable without any file-system or network side-effects.

    Args:
        trend_reader:    Adapter for reading processed TrendTopic files.
        brief_generator: Adapter for constructing ContentBrief entities.
        brief_storage:   Adapter for persisting briefs and batches.
    """

    def __init__(
        self,
        trend_reader: TrendReaderPort,
        brief_generator: BriefGeneratorPort,
        brief_storage: BriefStoragePort,
    ) -> None:
        self._trend_reader = trend_reader
        self._brief_generator = brief_generator
        self._brief_storage = brief_storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        source_filename: str | None = None,
        region: str | None = None,
    ) -> BriefBatch:
        """
        Run the full content brief generation pipeline.

        Args:
            source_filename: Specific processed trend filename to process.
                             If None, the most recently modified file is used.
            region:          ISO country code filter applied when
                             `source_filename` is None to narrow the file search.

        Returns:
            A completed BriefBatch. Will contain zero briefs if the source
            file was empty — this is not treated as an error.

        Raises:
            TrendFileNotFoundError: If no processable trend files are found.
            TrendFileParseError:    If the selected file cannot be deserialised.
            BriefGenerationError:   If brief construction fails for any topic.
            StorageError:           If persistence fails.
        """
        # ── Step 1: Resolve source file ───────────────────────────────
        topics: list[TrendTopic]
        resolved_filename: str

        if source_filename is not None:
            logger.info(
                "Processing explicit trend file: '%s'.", source_filename
            )
            topics = self._trend_reader.read_from_file(source_filename)
            resolved_filename = source_filename
        else:
            logger.info(
                "No file specified — resolving latest trend file  region_filter='%s'.",
                region or "all",
            )
            topics, resolved_filename = self._trend_reader.read_latest(region=region)

        # ── Step 2: Guard — empty source ─────────────────────────────
        if not topics:
            logger.warning(
                "Source file '%s' contains no topics. Returning empty batch.",
                resolved_filename,
            )
            return BriefBatch(
                region=self._extract_region(resolved_filename),
                source_trend_file=resolved_filename,
                briefs=[],
            )

        logger.info(
            "Loaded %d topic(s) from '%s'. Beginning brief generation.",
            len(topics),
            resolved_filename,
        )

        # ── Step 3: Generate briefs ───────────────────────────────────
        briefs: list[ContentBrief] = []
        for idx, topic in enumerate(topics, start=1):
            logger.info(
                "Generating brief %d/%d — '%s' (volume=%d, growing=%s).",
                idx,
                len(topics),
                topic.topic_name,
                topic.search_volume,
                topic.is_growing,
            )
            brief = self._brief_generator.generate(
                topic=topic,
                source_file=resolved_filename,
            )
            briefs.append(brief)
            logger.debug("Brief %s created.", brief.brief_id)

        # ── Step 4: Persist individual briefs ─────────────────────────
        for brief in briefs:
            saved = self._brief_storage.save_brief(brief)
            logger.info("Persisted individual brief → '%s'.", saved)

        # ── Step 5: Persist batch ─────────────────────────────────────
        batch = BriefBatch(
            region=self._extract_region(resolved_filename),
            source_trend_file=resolved_filename,
            briefs=briefs,
        )
        batch_file = self._brief_storage.save_batch(batch)
        logger.info(
            "Persisted batch of %d brief(s) → '%s'.",
            batch.brief_count,
            batch_file,
        )

        logger.info(
            "Content brief pipeline complete. %d brief(s) ready.", len(briefs)
        )
        return batch

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_region(filename: str) -> str:
        """
        Parse the ISO region code embedded in a processed trend filename.

        Expected pattern: ``processed_trends_{REGION}_{TIMESTAMP}.json``
        Example:          ``processed_trends_US_20240601T120000Z.json`` → "US"

        Falls back to "XX" on any parse failure so the pipeline never crashes
        on an unexpectedly named file.

        Args:
            filename: Bare filename (no directory prefix).

        Returns:
            2-letter uppercase ISO country code, or "XX" on parse failure.
        """
        try:
            stem = filename.replace(".json", "")
            parts = stem.split("_")
            candidate = parts[_FILENAME_REGION_SEGMENT_INDEX].upper()
            if len(candidate) == 2 and candidate.isalpha():
                return candidate
        except (IndexError, AttributeError):
            pass

        logger.warning(
            "Could not parse region from filename '%s'. Defaulting to 'XX'.",
            filename,
        )
        return "XX"
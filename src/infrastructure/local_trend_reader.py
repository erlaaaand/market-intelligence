# src/infrastructure/local_trend_reader.py

"""
Local filesystem adapter — concrete implementation of TrendReaderPort.

Scans the configured processed data directory for JSON files produced by
the upstream `TrendAnalyzerUseCase` (via `LocalStorageAdapter.save_processed`),
then deserialises each file back into validated `TrendTopic` domain entities.

Expected input file format:
    A JSON array, where each element is a TrendTopic serialised via
    `TrendTopic.model_dump(mode="json")`.

Robustness strategy:
    - Malformed individual records are skipped with a WARNING log rather than
      aborting the entire batch. The caller receives whatever valid records exist.
    - All I/O and validation errors are translated into domain exceptions before
      being raised, keeping infrastructure details out of the application layer.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from src.core.brief_ports import TrendReaderPort
from src.core.entities import TrendTopic
from src.core.exceptions import TrendFileNotFoundError, TrendFileParseError

logger = logging.getLogger(__name__)

# Filename prefix produced by TrendAnalyzerUseCase._PROCESSED_FILENAME_TEMPLATE.
_FILE_PREFIX: str = "processed_trends_"
_GLOB_PATTERN: str = f"{_FILE_PREFIX}*.json"


class LocalTrendReaderAdapter(TrendReaderPort):
    """
    Reads processed TrendTopic entities from the local filesystem.

    Directory layout (managed by the upstream LocalStorageAdapter):

        <processed_base_path>/
            processed_trends_US_20240601T120000Z.json
            processed_trends_ID_20240602T083000Z.json
            ...

    Files are selected by glob pattern and sorted by modification time
    (newest first) to ensure `read_latest` returns the most recent run.
    """

    def __init__(self, processed_base_path: str) -> None:
        """
        Args:
            processed_base_path: Filesystem directory containing processed JSON files.

        Raises:
            TrendFileNotFoundError: If the directory does not exist at init time.
        """
        self._base = Path(processed_base_path).resolve()
        if not self._base.exists():
            raise TrendFileNotFoundError(
                path=str(self._base),
                reason=(
                    "Processed data directory does not exist. "
                    "Run `python main.py --region <CC>` first to generate trend data."
                ),
            )
        logger.debug("LocalTrendReaderAdapter initialised → '%s'.", self._base)

    # ------------------------------------------------------------------
    # TrendReaderPort
    # ------------------------------------------------------------------

    def list_available_files(self, region: str | None = None) -> list[str]:
        """
        Return a list of processable trend filenames, newest-first.

        Applies an optional region filter by matching the ISO code embedded
        in the filename (e.g. ``processed_trends_US_...json`` → region "US").

        Args:
            region: Optional ISO 3166-1 alpha-2 country code filter.

        Returns:
            Bare filenames sorted by modification time (newest first).

        Raises:
            TrendFileNotFoundError: If no matching files are found.
        """
        candidates: list[Path] = sorted(
            self._base.glob(_GLOB_PATTERN),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if region is not None:
            region_upper = region.upper().strip()
            # Match the exact region segment: _US_ (with surrounding underscores).
            candidates = [p for p in candidates if f"_{region_upper}_" in p.name]

        if not candidates:
            suffix = f" for region '{region.upper()}'" if region else ""
            raise TrendFileNotFoundError(
                path=str(self._base),
                reason=(
                    f"No processed trend files found{suffix}. "
                    "Run `python main.py` to generate trend data first."
                ),
            )

        result = [p.name for p in candidates]
        logger.debug("Found %d trend file(s).", len(result))
        return result

    def read_from_file(self, filename: str) -> list[TrendTopic]:
        """
        Deserialise a specific processed trend JSON file.

        Malformed records within the array are skipped with a WARNING rather
        than raising, so a single bad record does not discard the entire batch.

        Args:
            filename: Bare filename within the processed data directory.

        Returns:
            List of validated TrendTopic entities (may be empty).

        Raises:
            TrendFileNotFoundError: If the file does not exist.
            TrendFileParseError:    If the JSON is structurally invalid.
        """
        target = self._base / filename
        if not target.exists():
            raise TrendFileNotFoundError(
                path=str(target),
                reason=f"Processed file '{filename}' not found in '{self._base}'.",
            )
        logger.debug("Reading trend file: '%s'.", target)
        return self._deserialise(target)

    def read_latest(self, region: str | None = None) -> tuple[list[TrendTopic], str]:
        """
        Load TrendTopic entities from the most recently modified trend file.

        Args:
            region: Optional ISO country code filter.

        Returns:
            A 2-tuple of (list[TrendTopic], bare source filename).

        Raises:
            TrendFileNotFoundError: If no matching files exist.
            TrendFileParseError:    If the selected file is malformed.
        """
        files = self.list_available_files(region=region)
        latest = files[0]  # Already sorted newest-first.
        logger.info("Auto-selected latest trend file: '%s'.", latest)
        return self.read_from_file(latest), latest

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _deserialise(self, path: Path) -> list[TrendTopic]:
        """
        Read, JSON-decode, and Pydantic-validate a processed trend file.

        Args:
            path: Absolute path to the JSON file.

        Returns:
            List of successfully validated TrendTopic entities.

        Raises:
            TrendFileParseError: On JSON syntax errors or if the root value
                                 is not a JSON array.
        """
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TrendFileParseError(
                path=str(path),
                reason=f"Cannot read file: {exc}",
            ) from exc

        try:
            payload: list[dict[str, object]] = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise TrendFileParseError(
                path=str(path),
                reason=f"Invalid JSON at line {exc.lineno}, col {exc.colno}: {exc.msg}",
            ) from exc

        if not isinstance(payload, list):
            raise TrendFileParseError(
                path=str(path),
                reason=(
                    f"Expected a JSON array at the root level, "
                    f"got {type(payload).__name__}."
                ),
            )

        topics: list[TrendTopic] = []
        for idx, record in enumerate(payload):
            try:
                topics.append(TrendTopic.model_validate(record))
            except ValidationError as exc:
                # Log but skip — partial data is better than no data.
                logger.warning(
                    "Skipping record %d in '%s': Pydantic validation failed — %s",
                    idx,
                    path.name,
                    exc,
                )

        logger.info(
            "Parsed %d/%d valid TrendTopic(s) from '%s'.",
            len(topics),
            len(payload),
            path.name,
        )
        return topics
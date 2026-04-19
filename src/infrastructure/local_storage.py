from __future__ import annotations

# src/infrastructure/local_storage.py

"""
LocalStorageAdapter — filesystem implementation of StoragePort (v2).

Folder layout
─────────────
Raw data (date-partitioned):
    <raw_base>/<YYYY-MM-DD>/<filename>.json

Processed reports (region + date partitioned):
    <processed_base>/<REGION>/<YYYY-MM-DD>/<filename>.json
    e.g.  data/processed/ID/2026-04-19/market_data_120045Z.json

Briefs (date-partitioned):
    <briefs_base>/<YYYY-MM-DD>/<filename>.json
    <briefs_base>/<YYYY-MM-DD>/individual/<filename>.json

All subdirectories are created lazily at write-time so a long-running
session that crosses midnight (or changes region) always lands in the
correct folder without any restart.

Writes are atomic: every file is first written to a ``.tmp`` sibling, then
renamed into place, so a crash mid-write never produces a corrupt JSON file.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.entities import MarketAnalysisReport
from src.core.exceptions import StorageError
from src.core.ports import StoragePort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON encoder
# ---------------------------------------------------------------------------

class _ISODateTimeEncoder(json.JSONEncoder):
    """Encode :class:`datetime` objects as ISO-8601 strings."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LocalStorageAdapter(StoragePort):
    """
    Filesystem storage adapter with automatic path partitioning.

    Processed reports are stored under ``<processed_base>/<REGION>/<DATE>/``
    so that results from different regions and days are cleanly separated and
    trivially discoverable by downstream consumers.

    Args:
        raw_base_path:       Root directory for raw JSON files.
        processed_base_path: Root directory for processed report files.
        briefs_base_path:    Root directory for content brief JSON files.
                             Pass ``None`` to disable brief storage.
    """

    def __init__(
        self,
        raw_base_path: str,
        processed_base_path: str,
        briefs_base_path: str | None = None,
    ) -> None:
        self._raw_path = Path(raw_base_path).resolve()
        self._processed_path = Path(processed_base_path).resolve()
        self._briefs_path = (
            Path(briefs_base_path).resolve() if briefs_base_path else None
        )
        self._ensure_base_directories()

    # ------------------------------------------------------------------
    # StoragePort interface
    # ------------------------------------------------------------------

    def save_raw(self, data: dict[str, object], filename: str) -> None:
        """
        Persist a raw payload under:
            ``<raw_base>/<YYYY-MM-DD>/<filename>``
        """
        target = self._dated_dir(self._raw_path) / filename
        self._write_json(target, data)
        logger.info("Raw data saved  → %s", target)

    def save_processed(
        self, report: MarketAnalysisReport, filename: str
    ) -> None:
        """
        Persist a ``MarketAnalysisReport`` under:
            ``<processed_base>/<REGION>/<YYYY-MM-DD>/<filename>``

        The region and date are read from ``report.metadata`` so the path
        reflects the actual data provenance rather than today's run date.

        Args:
            report:   Validated ``MarketAnalysisReport`` to serialise.
            filename: Target filename (e.g. ``market_data_120045Z.json``).
        """
        region = report.metadata.region     # e.g. "ID"
        date_str = report.metadata.date     # e.g. "2026-04-19"

        dated_dir = self._processed_path / region / date_str
        try:
            dated_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                path=str(dated_dir),
                reason=f"Cannot create processed directory: {exc}",
            ) from exc

        target = dated_dir / filename
        self._write_json(target, report.model_dump(mode="json"))
        logger.info("Processed report saved  → %s", target)

    # ------------------------------------------------------------------
    # Extended interface (briefs)
    # ------------------------------------------------------------------

    def save_brief(self, data: dict[str, object], filename: str) -> None:
        """Persist a batch brief summary under ``<briefs>/<YYYY-MM-DD>/``."""
        target = self._dated_dir(self._briefs_dir()) / filename
        self._write_json(target, data)
        logger.info("Brief saved  → %s", target)

    def save_brief_individual(
        self, data: dict[str, object], filename: str
    ) -> None:
        """Persist an individual brief under ``<briefs>/<YYYY-MM-DD>/individual/``."""
        individual_dir = self._dated_dir(self._briefs_dir()) / "individual"
        try:
            individual_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                path=str(individual_dir),
                reason=f"Cannot create individual brief directory: {exc}",
            ) from exc
        target = individual_dir / filename
        self._write_json(target, data)
        logger.info("Individual brief saved  → %s", target)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _briefs_dir(self) -> Path:
        if self._briefs_path is None:
            raise StorageError(
                path="<briefs_base_path>",
                reason=(
                    "briefs_base_path was not configured. "
                    "Pass briefs_base_path= to LocalStorageAdapter()."
                ),
            )
        return self._briefs_path

    @staticmethod
    def _today_utc() -> str:
        """Return today's UTC date as ``YYYY-MM-DD``."""
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _dated_dir(self, base: Path) -> Path:
        """Return ``<base>/<YYYY-MM-DD>``, creating it if absent."""
        dated = base / self._today_utc()
        try:
            dated.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                path=str(dated),
                reason=f"Cannot create dated directory: {exc}",
            ) from exc
        return dated

    def _ensure_base_directories(self) -> None:
        """Create root base directories eagerly at construction time."""
        bases = [self._raw_path, self._processed_path]
        if self._briefs_path is not None:
            bases.append(self._briefs_path)

        for directory in bases:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Base directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create base directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        """
        Atomically write *payload* as indented JSON to *target*.

        Uses a ``.tmp`` sibling + ``replace()`` so a crash mid-write never
        leaves a corrupt or truncated JSON file.
        """
        tmp = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_ISODateTimeEncoder)
            tmp.write_text(serialised, encoding="utf-8")
            tmp.replace(target)
            logger.debug("Written %d bytes  → %s", len(serialised), target)
        except (OSError, TypeError, ValueError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(path=str(target), reason=str(exc)) from exc
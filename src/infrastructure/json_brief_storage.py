# src/infrastructure/json_brief_storage.py

"""
JSON filesystem adapter — concrete implementation of BriefStoragePort.

Persists ContentBrief entities as pretty-printed JSON files using the same
atomic-write strategy (tmp → rename) as the upstream LocalStorageAdapter.

Directory layout managed by this adapter:

    <base_path>/
        individual/
            brief_<uuid>.json         ← one file per ContentBrief
        batch_briefs_<region>_<ts>.json  ← full BriefBatch per pipeline run
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.core.brief_entities import BriefBatch, ContentBrief
from src.core.brief_ports import BriefStoragePort
from src.core.exceptions import StorageError

logger = logging.getLogger(__name__)

_INDIVIDUAL_SUBDIR: str = "individual"
_BRIEF_FILENAME_TMPL: str = "brief_{brief_id}.json"
_BATCH_FILENAME_TMPL: str = "batch_briefs_{region}_{ts}.json"


class _ISODateTimeEncoder(json.JSONEncoder):
    """Extend the default JSON encoder to serialise datetime → ISO 8601 string."""

    def default(self, obj: object) -> str | object:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class JsonBriefStorageAdapter(BriefStoragePort):
    """
    Persists ContentBrief and BriefBatch entities as pretty-printed JSON.

    Two separate sub-paths are maintained:
        individual/: one file per brief (brief_<uuid>.json).
        root:        one batch file per pipeline run.

    Both directories are created on demand. Writes are atomic via a
    temporary sibling file and `Path.replace()`.
    """

    def __init__(self, base_path: str) -> None:
        """
        Args:
            base_path: Root filesystem directory for brief output files.

        Raises:
            StorageError: If the directories cannot be created.
        """
        self._base = Path(base_path).resolve()
        self._individual = self._base / _INDIVIDUAL_SUBDIR
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # BriefStoragePort implementation
    # ------------------------------------------------------------------

    def save_brief(self, brief: ContentBrief) -> str:
        """
        Write a single ContentBrief to <base_path>/individual/brief_<uuid>.json.

        Args:
            brief: Validated ContentBrief entity.

        Returns:
            Bare filename of the saved file (e.g. 'brief_abc123…json').

        Raises:
            StorageError: If the write operation fails.
        """
        filename = _BRIEF_FILENAME_TMPL.format(brief_id=brief.brief_id)
        target = self._individual / filename
        self._write_json(target, brief.model_dump(mode="json"))
        logger.info("Individual brief saved → %s", target)
        return filename

    def save_batch(self, batch: BriefBatch) -> str:
        """
        Write a BriefBatch to <base_path>/batch_briefs_<region>_<ts>.json.

        Args:
            batch: BriefBatch entity containing all briefs for the pipeline run.

        Returns:
            Bare filename of the saved batch file.

        Raises:
            StorageError: If the write operation fails.
        """
        ts = batch.generated_at.strftime("%Y%m%dT%H%M%SZ")
        filename = _BATCH_FILENAME_TMPL.format(region=batch.region, ts=ts)
        target = self._base / filename
        self._write_json(target, batch.model_dump(mode="json"))
        logger.info("Batch file saved → %s", target)
        return filename

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        """Create output directories (and all parents) if absent."""
        for directory in (self._base, self._individual):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create output directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        """
        Serialise *payload* and write atomically to *target*.

        Strategy: write to a `.tmp` sibling, then `Path.replace()` to the final
        destination. This prevents partial writes from corrupting existing files
        on crash or disk-full errors.

        Args:
            target:  Absolute destination path.
            payload: JSON-serialisable object (dict or list).

        Raises:
            StorageError: On any I/O or serialisation failure.
        """
        tmp = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_ISODateTimeEncoder)
            tmp.write_text(serialised, encoding="utf-8")
            tmp.replace(target)
            logger.debug("Wrote %d bytes → %s", len(serialised), target)
        except (OSError, TypeError, ValueError) as exc:
            # Best-effort cleanup of the temp file.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(
                path=str(target),
                reason=str(exc),
            ) from exc
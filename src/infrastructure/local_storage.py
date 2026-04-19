# src/infrastructure/local_storage.py

"""
Local filesystem adapter — concrete implementation of `StoragePort`.

Serialises domain entities to JSON and writes them to configurable
directories. Creates intermediate directories on demand so the caller
never needs to manage the file-system layout manually.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from src.core.entities import TrendTopic
from src.core.exceptions import StorageError
from src.core.ports import StoragePort

logger = logging.getLogger(__name__)


class _DateTimeEncoder(json.JSONEncoder):
    """Extend the default JSON encoder to serialise `datetime` objects."""

    def default(self, obj: object) -> str | object:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class LocalStorageAdapter(StoragePort):
    """
    Persists data as pretty-printed JSON files on the local file system.

    Two separate base directories are maintained:
      - `raw_base_path`:       for raw provider payloads.
      - `processed_base_path`: for processed `TrendTopic` entities.

    Both directories (and their parents) are created automatically if they
    do not already exist.
    """

    def __init__(self, raw_base_path: str, processed_base_path: str) -> None:
        """
        Initialise the adapter with target directory paths.

        Args:
            raw_base_path:       Filesystem path for raw JSON output.
            processed_base_path: Filesystem path for processed JSON output.
        """
        self._raw_path = Path(raw_base_path)
        self._processed_path = Path(processed_base_path)

        self._ensure_directories()

    # ------------------------------------------------------------------
    # StoragePort implementation
    # ------------------------------------------------------------------

    def save_raw(self, data: dict[str, object], filename: str) -> None:
        """
        Write a raw data dictionary to `raw_base_path/<filename>`.

        Args:
            data:     JSON-serialisable dictionary to persist.
            filename: Target filename (e.g. "raw_trends_US_20240601T120000Z.json").

        Raises:
            StorageError: If the file cannot be written.
        """
        target = self._raw_path / filename
        self._write_json(target, data)
        logger.info("Raw data saved → %s", target)

    def save_processed(self, data: list[TrendTopic], filename: str) -> None:
        """
        Serialise a list of `TrendTopic` entities and write to
        `processed_base_path/<filename>`.

        Args:
            data:     List of processed trend entities.
            filename: Target filename.

        Raises:
            StorageError: If the file cannot be written.
        """
        payload: list[dict[str, object]] = [
            topic.model_dump(mode="json") for topic in data
        ]
        target = self._processed_path / filename
        self._write_json(target, payload)
        logger.info("Processed data saved → %s", target)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_directories(self) -> None:
        """Create raw and processed directories (including parents) if absent."""
        for directory in (self._raw_path, self._processed_path):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        """
        Serialise `payload` to JSON and write it atomically-ish to `target`.

        Uses a temporary sibling file and `Path.replace` to minimise the
        risk of partial writes corrupting an existing file.

        Args:
            target:  Absolute path for the output file.
            payload: JSON-serialisable object.

        Raises:
            StorageError: On any I/O or serialisation error.
        """
        tmp_target = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_DateTimeEncoder)
            tmp_target.write_text(serialised, encoding="utf-8")
            tmp_target.replace(target)
        except (OSError, TypeError, ValueError) as exc:
            # Clean up temporary file if it exists
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
            raise StorageError(
                path=str(target),
                reason=str(exc),
            ) from exc
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.entities import CreativeDocumentBatch
from src.core.exceptions import StorageError
from src.core.ports import StoragePort

logger = logging.getLogger(__name__)


class _ISODateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class LocalStorageAdapter(StoragePort):
    def __init__(
        self,
        raw_base_path: str,
        processed_base_path: str,
    ) -> None:
        self._raw_path = Path(raw_base_path).resolve()
        self._processed_path = Path(processed_base_path).resolve()
        self._ensure_base_directories()

    def save_raw(self, data: dict[str, object], filename: str) -> None:
        target = self._dated_dir(self._raw_path) / filename
        self._write_json(target, data)
        logger.info("Raw data saved  → %s", target)

    def save_processed(
        self, batch: CreativeDocumentBatch, filename: str
    ) -> None:
        target_dir = self._processed_path / batch.region / batch.date

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                path=str(target_dir),
                reason=f"Cannot create processed directory: {exc}",
            ) from exc

        target = target_dir / filename
        self._write_json(target, batch.model_dump(mode="json"))
        logger.info("Processed batch saved  → %s", target)

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _dated_dir(self, base: Path) -> Path:
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
        for directory in [self._raw_path, self._processed_path]:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Base directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create base directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        tmp = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_ISODateTimeEncoder)
            tmp.write_text(serialised, encoding="utf-8")
            tmp.replace(target)
            logger.debug("Written %d bytes  → %s", target)
        except (OSError, TypeError, ValueError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError(path=str(target), reason=str(exc)) from exc
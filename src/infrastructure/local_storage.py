from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.entities import TrendTopic
from src.core.exceptions import StorageError
from src.core.ports import StoragePort

logger = logging.getLogger(__name__)


class _ISODateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> str | Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class LocalStorageAdapter(StoragePort):

    def __init__(
        self,
        raw_base_path: str,
        processed_base_path: str,
        briefs_base_path: str | None = None,
    ) -> None:
        self._raw_path = Path(raw_base_path).resolve()
        self._processed_path = Path(processed_base_path).resolve()
        self._briefs_path = Path(briefs_base_path).resolve() if briefs_base_path else None
        self._ensure_directories()

    def save_raw(self, data: dict[str, object], filename: str) -> None:
        target = self._raw_path / filename
        self._write_json(target, data)
        logger.info("Raw data saved → %s", target)

    def save_processed(self, data: list[TrendTopic], filename: str) -> None:
        payload: list[dict[str, object]] = [
            topic.model_dump(mode="json") for topic in data
        ]
        target = self._processed_path / filename
        self._write_json(target, payload)
        logger.info("Processed data saved → %s", target)

    def save_brief(self, data: dict[str, object], filename: str) -> None:
        if self._briefs_path is None:
            raise StorageError(
                path="<briefs_base_path>",
                reason="briefs_base_path was not configured on this adapter instance.",
            )
        target = self._briefs_path / filename
        self._write_json(target, data)
        logger.info("Brief saved → %s", target)

    def _ensure_directories(self) -> None:
        directories = [self._raw_path, self._processed_path]
        if self._briefs_path is not None:
            directories.append(self._briefs_path)
            directories.append(self._briefs_path / "individual")

        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                logger.debug("Directory ensured: %s", directory)
            except OSError as exc:
                raise StorageError(
                    path=str(directory),
                    reason=f"Cannot create directory: {exc}",
                ) from exc

    def _write_json(self, target: Path, payload: object) -> None:
        tmp_target = target.with_suffix(".tmp")
        try:
            serialised = json.dumps(payload, indent=2, cls=_ISODateTimeEncoder)
            tmp_target.write_text(serialised, encoding="utf-8")
            tmp_target.replace(target)
            logger.debug("Written %d bytes → %s", len(serialised), target)
        except (OSError, TypeError, ValueError) as exc:
            if tmp_target.exists():
                try:
                    tmp_target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StorageError(
                path=str(target),
                reason=str(exc),
            ) from exc
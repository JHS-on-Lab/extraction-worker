"""
FileSink — CollectedContent 을 JSONL 파일에 기록.

파티셔닝: {FILE_SINK_DIR}/{YYYY-MM-DD}/{source_type}-{worker_id}.jsonl
worker-id 별로 파일을 분리해 여러 extractor 가 동시에 써도 충돌하지 않는다.
"""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

from app import config
from app.sink.serialize import to_doc
from app.types import CollectedContent


class FileSink:
    def __init__(
        self,
        crawler_type: str,
        crawl_runtime_key: str,
        base_dir: str | None = None,
    ) -> None:
        self._crawler_type      = crawler_type
        self._crawl_runtime_key = crawl_runtime_key
        self._base              = Path(base_dir or config.FILE_SINK_DIR)

    def write(self, content: CollectedContent) -> None:
        date_str = content.collected_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        out_dir = self._base / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{content.source_type}-{config.WORKER_ID}.jsonl"

        row = to_doc(content, self._crawler_type, self._crawl_runtime_key)

        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

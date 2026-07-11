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
    # 매 write() 가 파일에 즉시 append+flush 되어 이미 durable 하므로 배치가
    # 필요 없다 — extraction_worker.py 가 batch_size 를 기준으로 flush 타이밍을
    # 정하므로, 1로 두면 매 write() 직후 바로 flush()(no-op)가 호출되고 곧바로
    # mark_stored 로 이어져 SolrSink 도입 이전과 동일한 즉시-반영 동작을 유지한다.
    batch_size = 1

    def __init__(
        self,
        crawler_type: str,
        crawl_runtime_key: str,
        base_dir: str | None = None,
    ) -> None:
        self._crawler_type      = crawler_type
        self._crawl_runtime_key = crawl_runtime_key
        self._base              = Path(base_dir or config.FILE_SINK_DIR)

    def flush(self) -> None:
        """파일에는 write() 시점에 이미 즉시 기록되므로 할 일이 없다."""
        pass

    def write(self, content: CollectedContent) -> None:
        date_str = content.collected_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        out_dir = self._base / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{content.source_type}-{config.WORKER_ID}.jsonl"

        row = to_doc(content, self._crawler_type, self._crawl_runtime_key)

        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

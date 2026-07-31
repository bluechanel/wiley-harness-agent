"""严格审计日志:append-only JSONL,逐条 flush。

记录语义级事件(请求、响应、工具调用/结果、压缩、错误)而非逐个
增量 delta;每条一行 JSON,写后即 flush(不 fsync)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    """把 agent 的每一步留痕到 JSONL 文件,append 打开、只增不改。

    记录格式:``{"ts": <UTC ISO8601>, "kind": ..., **data}``,中文原样
    (ensure_ascii=False)。路径按调用方 CWD 解析,可显式传 path 覆盖。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    @classmethod
    def default(cls) -> AuditLog:
        """默认落在 CWD 的 .wy_audit/ 下,文件名含 UTC 时间与随机后缀。"""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
        return cls(Path.cwd() / ".wy_audit" / name)

    def write(self, kind: str, data: Mapping) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

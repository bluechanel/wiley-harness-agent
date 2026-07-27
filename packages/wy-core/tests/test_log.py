"""log 模块:JSONL 审计文件行为。"""

import json

from wy_core import AuditLog


def test_写入与字段(tmp_path):
    log = AuditLog(tmp_path / "a" / "b" / "audit.jsonl")  # 自动建父目录
    log.write("agent_start", {"model": "fake", "tools": ["echo"]})
    log.write("request", {"messages": [], "备注": "中文键值"})
    log.close()

    lines = (tmp_path / "a" / "b" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "agent_start" and first["model"] == "fake"
    assert "ts" in first
    assert "中文键值" in lines[1]  # ensure_ascii=False,中文原样落盘


def test_追加打开不覆盖(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    first.write("a", {})
    first.close()
    second = AuditLog(path)
    second.write("b", {})
    second.close()
    kinds = [json.loads(line)["kind"] for line in path.read_text().splitlines()]
    assert kinds == ["a", "b"]


def test_default_落在_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = AuditLog.default()
    log.write("x", {})
    log.close()
    assert log.path.parent == tmp_path / ".wy_audit"
    assert log.path.suffix == ".jsonl"

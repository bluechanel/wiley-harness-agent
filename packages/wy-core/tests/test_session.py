"""session 模块:用量记录、压缩触发与压缩算法。"""

import asyncio

from wy_core import Message, Session, TextBlock, ToolResultBlock, ToolUseBlock, Usage, user_message

from helpers import FakeModel, make_text_end


def _assistant(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text)])


def _tool_pair(call_id: str) -> tuple[Message, Message]:
    """一对相邻的 tool_use / tool_result 消息。"""
    use = Message(role="assistant", content=[ToolUseBlock(id=call_id, name="echo", input={})])
    result = Message(role="user", content=[ToolResultBlock(tool_use_id=call_id, content="ok")])
    return use, result


def test_record_usage_更新上下文并累计():
    session = Session()
    session.record_usage(Usage(input_tokens=100, output_tokens=10))
    session.record_usage(Usage(input_tokens=120, output_tokens=5, cache_read_tokens=30))
    assert session.context_tokens == 155  # 取最近一次,不累计
    assert session.total_usage.input_tokens == 220


def test_needs_compaction_阈值与可分割性():
    session = Session(max_context_tokens=100, keep_recent=2)
    for i in range(4):
        session.append(user_message(f"m{i}"))
    assert not session.needs_compaction()  # 未到阈值
    session.record_usage(Usage(input_tokens=100))
    assert session.needs_compaction()

    small = Session(max_context_tokens=100, keep_recent=8)
    small.append(user_message("仅一条"))
    small.record_usage(Usage(input_tokens=100))
    assert not small.needs_compaction()  # 无可压缩的历史,放弃


def test_compact_替换历史并归零上下文():
    session = Session(keep_recent=2)
    for i in range(2):
        session.append(user_message(f"问{i}"))
        session.append(_assistant(f"答{i}"))
    session.append(user_message("问2"))
    session.append(_assistant("答2"))
    session.record_usage(Usage(input_tokens=500))

    model = FakeModel([[make_text_end("这是摘要")]])
    info = asyncio.run(session.compact(model))

    assert info == {"dropped": 4, "summary": "这是摘要"}
    assert len(session.messages) == 3
    assert session.messages[0].role == "user"
    assert session.messages[0].text == "[早前对话摘要]\n这是摘要"
    assert session.messages[1].text == "问2"
    assert session.context_tokens == 0
    # 压缩请求本身:单条 user 消息携带纯文本转写,带压缩 system 提示词
    call = model.calls[0]
    assert call["tools"] is None
    assert "压缩" in call["system"]
    assert "[user]" in call["messages"][0].text and "答1" in call["messages"][0].text


def test_compact_不拆散工具对():
    session = Session(keep_recent=2)
    session.append(user_message("问0"))
    session.append(_assistant("答0"))
    session.append(user_message("问1"))
    use, result = _tool_pair("t1")
    session.append(use)
    session.append(result)
    session.append(_assistant("答1"))
    # split 原本落在 tool_result(index 4),必须前移到 tool_use 之前
    model = FakeModel([[make_text_end("摘要")]])
    info = asyncio.run(session.compact(model))

    assert info["dropped"] == 3
    kept = session.messages[1:]
    assert kept[0] is use and kept[1] is result and kept[2].text == "答1"
    # 转写里包含工具调用与结果的文字形态
    transcript = model.calls[0]["messages"][0].text
    assert "调用工具" not in transcript  # 工具对被保留,未进入被压缩段
    assert "答0" in transcript

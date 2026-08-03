"""message 模块:块序列化、Message.text 与 Usage。"""

import json

from wy_core import Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock, Usage, user_message


def test_to_dict_是自描述且可_json_序列化():
    message = Message(
        role="assistant",
        content=[
            ThinkingBlock(thinking="想一想", signature="sig"),
            TextBlock(text="你好"),
            ToolUseBlock(id="t1", name="echo", input={"text": "hi"}),
        ],
    )
    data = message.to_dict()
    assert data["role"] == "assistant"
    assert [b["type"] for b in data["content"]] == ["thinking", "text", "tool_use"]
    json.dumps(data, ensure_ascii=False)  # 不抛即通过


def test_text_只拼接文本块():
    message = Message(
        role="assistant",
        content=[
            ThinkingBlock(thinking="思考"),
            TextBlock(text="你"),
            ToolResultBlock(tool_use_id="t1", content="结果"),
            TextBlock(text="好"),
        ],
    )
    assert message.text == "你好"


def test_user_message():
    message = user_message("提问")
    assert message.role == "user"
    assert message.text == "提问"
    assert message.content == [TextBlock("提问")]


def test_user_message_追加_system_reminder_块():
    message = user_message("提问", reminders=["处于 plan 模式", "文件已变更"])
    assert [b.text for b in message.content] == [
        "提问",
        "<system-reminder>\n处于 plan 模式\n</system-reminder>",
        "<system-reminder>\n文件已变更\n</system-reminder>",
    ]


def test_usage_累计与上下文规模():
    total = Usage()
    total.add(Usage(input_tokens=100, output_tokens=20, cache_read_tokens=30, cache_write_tokens=5))
    total.add(Usage(input_tokens=1, output_tokens=2))
    assert total == Usage(input_tokens=101, output_tokens=22, cache_read_tokens=30, cache_write_tokens=5)
    assert total.context_tokens == 101 + 22 + 30 + 5

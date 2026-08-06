"""wy-core:极简 agent core runtime。

使用方式:继承 ``Model`` 适配任意 LLM API、继承 ``Tool`` 增加工具,
即得到完整 harness agent::

    agent = Agent(model=MyModel(api_key=...), tools=[MyTool()], system="...")
    async for event in agent.run("帮我看看这个仓库"):
        match event:
            case TextDelta(text=text): ...   # 实时渲染正文
            case ToolCall(name=name):  ...   # 展示工具调用
            case TurnEnd():            ...   # 回合结束

端到端语音模型同理:继承 ``RealtimeModel`` 适配厂商实时协议(把 wire
事件翻译为类型化事件)、实现 ``AudioSource``/``AudioSink`` 接音频设备,
交给 ``RealtimeAgent`` 即得到完整实时语音 agent(打断、回声抑制、
收集式 function calling、后台文字指令注入)。

会话历史与自动上下文压缩在 ``Session``;审计日志默认写入
CWD/.wy_audit/(``audit=None`` 关闭)。
"""

from wy_core.agent import (
    Agent,
    AgentError,
    AgentEvent,
    Compaction,
    TurnEnd,
)
from wy_core.audio import AudioSink, AudioSource
from wy_core.log import AuditLog
from wy_core.message import (
    Block,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    user_message,
)
from wy_core.model import (
    Model,
    ModelEnd,
    ModelError,
    ModelEvent,
    TextDelta,
    ThinkingDelta,
)
from wy_core.realtime_agent import (
    Interrupted,
    RealtimeAgent,
    RealtimeEvent,
    SessionEnded,
    ToolResultsSubmitted,
)
from wy_core.realtime_model import (
    AssistantTranscript,
    AssistantTranscriptDelta,
    AudioDelta,
    ErrorEvent,
    FunctionCall,
    RealtimeError,
    RealtimeModel,
    RealtimeModelEvent,
    ResponseDone,
    ResponseStarted,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    TurnCommitted,
    TurnDiscarded,
    UserTranscript,
    UserTranscriptDelta,
)
from wy_core.session import Session
from wy_core.state import AgentState, StateExtension
from wy_core.tool import Tool, ToolApproval, ToolCall, ToolHook, ToolResult

__all__ = [
    "Agent",
    "AgentError",
    "AgentEvent",
    "AgentState",
    "AssistantTranscript",
    "AssistantTranscriptDelta",
    "AudioDelta",
    "AudioSink",
    "AudioSource",
    "AuditLog",
    "Block",
    "Compaction",
    "ErrorEvent",
    "FunctionCall",
    "Interrupted",
    "Message",
    "Model",
    "ModelEnd",
    "ModelError",
    "ModelEvent",
    "RealtimeAgent",
    "RealtimeError",
    "RealtimeEvent",
    "RealtimeModel",
    "RealtimeModelEvent",
    "ResponseDone",
    "ResponseStarted",
    "Session",
    "SessionEnded",
    "SessionReady",
    "SpeechStarted",
    "SpeechStopped",
    "StateExtension",
    "TextBlock",
    "TextDelta",
    "ThinkingBlock",
    "ThinkingDelta",
    "Tool",
    "ToolApproval",
    "ToolCall",
    "ToolHook",
    "ToolResult",
    "ToolResultBlock",
    "ToolResultsSubmitted",
    "ToolUseBlock",
    "TurnCommitted",
    "TurnDiscarded",
    "TurnEnd",
    "Usage",
    "UserTranscript",
    "UserTranscriptDelta",
    "user_message",
]

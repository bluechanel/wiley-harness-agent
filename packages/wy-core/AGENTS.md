# wy-core Module Conventions

包定位与 workspace 结构见仓库根 `AGENTS.md`;本文件是 `wy_core` 包内的模块约定。

本包 `README.md` 是面向外部使用方（含 AI）的 SDK 使用文档，经 pyproject 的 `readme` 字段随 wheel 元数据发布：公开导出、契约义务（Model/Tool docstring）、事件语义或默认行为变更时必须同步更新它。

## 总原则

- 零运行时依赖(纯标准库),禁止 import 本仓其他包;首要目标是代码简洁易懂,每个模块保持小体量(目标 <150 行;`realtime_agent.py` 是知情例外,编排状态机不拆)。
- 包内模块以 `from wy_core.<mod> import ...` 子模块路径互引;对外 API 全部经 `wy_core/__init__.py` 重导出,增删导出或改签名即是修改对外契约。
- 依赖方向:文本回合侧 `agent → session/log/model/tool → message`,实时侧 `realtime_agent → realtime_model/audio/log/tool`(`realtime_model → tool`),无环;`session` 允许依赖 `model`(压缩需要调模型)。
- 中文 docstring;契约义务写在 ABC 的 docstring 里(docstring 即约定本体)。

## 模块职责

- `message.py` 统一消息词汇:Anthropic 风格中立块(text/thinking/tool_use/tool_result)、`Message`、`Usage`。全库(模型契约、会话历史、agent 事件、审计日志)只用这一套;非 Anthropic 后端由 Model 实现自行双向翻译。块联合允许应用侧扩展:核心逐块 isinstance 判断,未知块随消息原样携带(如 wy-coding-agent 的 `RedactedThinkingBlock`),由 Model 实现负责其 wire 翻译。
- `tool.py` `Tool` 抽象父类:`name`/`description`/`parameters`(JSON Schema)三个类属性 + 同步 `execute`(Agent 经 `asyncio.to_thread` 执行,允许阻塞);失败直接 raise,由 Agent 转 `Error: ...` 的 tool_result(`is_error=True`),不中断回合。另有两类 agent(回合式与实时)共用的工具执行事件 `ToolCall`/`ToolResult` 与 Tool 同居本模块。
- `model.py` `Model` 抽象父类:`stream(messages, *, system, tools) -> AsyncIterator[ModelEvent]` 签名固定,厂商参数全部在实现类构造期注入。事件仅 `TextDelta`/`ThinkingDelta`/`ModelEnd` 三种:增量只供实时渲染,**实现方负责组装完整 assistant 消息放进 `ModelEnd`**(刻意取舍:核心不做 JSON 增量拼装,厂商特有字段如 thinking signature 封在实现内;参考实现见 wy-coding-agent 的 `AnthropicModel`);`stop_reason == "tool_use"` 驱动工具循环,其余取值一律回合结束;传输/解码/流内错误一律 raise `ModelError`。
- `session.py` 内存态上下文(落盘与恢复由使用方实现):消息列表、`context_tokens`(最近一次请求的上下文规模)与 `total_usage`(累计);`needs_compaction`/`compact` 实现自动压缩——保留最近 `keep_recent` 条(分割点前移保证不拆散 tool_use/tool_result 对),更早历史转写为纯文本请模型总结,替换为一条 `[早前对话摘要]` user 消息,`context_tokens` 归零待下轮刷新。
- `log.py` `AuditLog` 审计日志:append-only JSONL、逐条 flush(不 fsync)、`ensure_ascii=False`;记录语义事件(`agent_start`/`request`/`model_end`/`tool_call`/`tool_result`/`compaction`/`error`),不逐 delta 留痕。默认路径为调用方 CWD 的 `.wy_audit/`,可显式传 path 覆盖。
- `agent.py` `Agent` 循环:压缩检查(只发生在两次请求之间)→ 模型流(增量原样透出,捕获 `ModelEnd`)→ 并发执行 tool_use(先按调用顺序产出全部 `ToolCall`,`asyncio.gather` 并发执行,再按调用顺序产出 `ToolResult`)→ 结果并成一条 user 消息回填,直到非 `tool_use` 以 `TurnEnd` 收尾;`max_iterations` 是失控循环保险丝,超限抛 `AgentError`。回合内任何异常(含消费方中途关闭流):统一在出口写一条 error 审计,并回滚本回合追加的消息保持会话完整(本回合已压缩过则历史结构已变,跳过回滚)。审计默认开启(省略 `audit` 参数),显式 `audit=None` 关闭;单实例不支持并发 `run`。
- `realtime_model.py` 实时(端到端语音)模型契约:`RealtimeModel` 抽象父类 + 类型化事件词汇(`SessionReady`/`ResponseStarted`/`AudioDelta`/`SpeechStarted`/`SpeechStopped`/`TurnCommitted`/`UserTranscriptDelta`/`UserTranscript`/`AssistantTranscriptDelta`/`AssistantTranscript`/`TurnDiscarded`/`FunctionCall`/`ResponseDone`/`ErrorEvent` = `RealtimeModelEvent`)+ `RealtimeError`。适配服务端持上下文、VAD 主动触发响应的推送式全双工协议,与 `Model.stream` 拉取式契约并列。实现方义务(docstring 即约定本体):厂商参数构造期注入;wire 事件自行翻译为上述词汇、词汇之外不产出;转写增量(`*TranscriptDelta`,`UserTranscriptDelta` 带 `stash` 暂存尾部)与生命周期信号(`SessionReady`/`SpeechStopped(reason)`/`TurnCommitted`)按厂商能力尽力产出、编排正确性不依赖,权威全文以完成级转写为准;`AudioDelta.pcm` 已解码、`FunctionCall.arguments` 已解析(残缺回退空 dict);正常关闭 `events()` 自然结束、传输失败一律 raise `RealtimeError`(未建连时发送类方法同样);`update_session` 建连后被调恰好一次并返回实发载荷供审计;`close()` 幂等。
- `audio.py` 流式音频 IO 抽象:`AudioSource`(start/read(timeout)→bytes|None/stop,read 同步阻塞由 agent 经 to_thread 调,超时回 None)与 `AudioSink`(start/play/clear/is_playing/stop,play 不得阻塞事件循环,clear 供打断,is_playing 供回声抑制)。PCM 一律 int16 单声道字节;采样率不属于契约,由音频实现与 RealtimeModel 实现两侧自行约定。
- `realtime_agent.py` `RealtimeAgent` 实时编排:`run()` 建连 → 一次 `update_session`(审计其返回载荷,kind=session_update)→ 起 `_send_audio` 任务 + 消费 `events()`,产出 `RealtimeEvent`——除 `FunctionCall`(收集待执行)与被抑制的残余增量外模型事件一律原样透传(含 `SessionReady`/`SpeechStarted`/`SpeechStopped`/`TurnCommitted`/`TurnDiscarded`/`ResponseStarted`/`AudioDelta`/转写四事件/`ResponseDone`/`ErrorEvent`),加编排自产的 `ToolCall`/`ToolResult`/`ToolResultsSubmitted`(工具结果全部回写并触发二轮后产出)/`Interrupted`/`SessionEnded`,覆盖完整生命周期供下游状态管理;编排正确性不依赖可选生命周期信号;`RealtimeError` → `SessionEnded` 收尾,其余异常写 error 审计后上抛,出口统一停发送任务/停音频/`model.close()`。关键语义:**打断**(SpeechStarted → speaker.clear,回复中再 cancel_response 并抑制残余 AudioDelta 与 AssistantTranscriptDelta 到下个 ResponseStarted);**回声抑制**(echo_suppression=True 闭麦+0.5s 冷却不支持打断;False 耳机模式能量门限 500 滤回声、高能量照发支持打断);**收集式 function calling**(FunctionCall 先收集,非 cancelled 的 ResponseDone 后统一顺序执行、逐个 send_tool_result、最后只发一次 create_response;cancelled 丢弃未执行调用);**send_user_text 后台指令注入**(空闲即注入+触发响应;在听(SpeechStarted 起至 ResponseStarted 或 TurnDiscarded 止)/在答/响应待建一律入队,回合真正结束按序补发、只触发一次响应;ErrorEvent 兜底清"响应待建"防队列卡死)。工具执行语义与 Agent 一致;mic/speaker 必填;audit 哨兵默认开启;审计 kinds:agent_start/session_update/user_transcript/assistant_transcript/user_text/interrupted/tool_call/tool_result/error(增量与生命周期信号不留痕)。无 Session/压缩(服务端持上下文)。

## 消费方接入示例

```python
from wy_core import Agent, Model, ModelEnd, TextDelta, Tool

class MyModel(Model):
    name = "my-llm"

    def __init__(self, api_key: str): ...

    async def stream(self, messages, *, system=None, tools=None):
        ...  # 调厂商 API:边收边 yield TextDelta,结束时 yield ModelEnd(完整消息+用量+停止原因)

class ReadFile(Tool):
    name = "read_file"
    description = "读取文件内容"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def execute(self, input: dict) -> str:
        with open(input["path"], encoding="utf-8") as f:
            return f.read()

agent = Agent(model=MyModel(api_key="..."), tools=[ReadFile()], system="你是编码助手")
async for event in agent.run("读一下 README"):
    ...
```

## 测试

- 测试在 `tests/`,不引入 pytest-asyncio:async 流程用 `helpers.run_events`(内部 `asyncio.run`)收集事件;`FakeModel` 按脚本吐事件组并记录每次收到的请求供断言。
- 实时编排用 `core_realtime_helpers.py` 驱动:`FakeRealtimeModel` 按脚本吐类型化事件(脚本项可为事件、`WaitFor` 谓词同步项或异常),发送类调用按序记录进 `sent`;`run_realtime` 可传 async `on_event` 回调在事件产出点中途驱动 agent(如注入文字指令);假音频件 `FakeMic`/`FakeSpeaker`。测试文件与辅助模块名跨包必须唯一(app 侧已占用 `realtime_helpers.py`/`test_realtime_*`)。
- 涉及默认审计的用例必须 `monkeypatch.chdir(tmp_path)`;其余用例一律传 `audit=None`,避免把审计文件写进仓库目录。

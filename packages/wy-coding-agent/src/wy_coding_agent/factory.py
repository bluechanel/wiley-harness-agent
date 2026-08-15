"""Module entry point that assembles a ready-to-use agent."""

from collections.abc import Sequence
from pathlib import Path

from wy_core import Agent, AgentState, AuditLog, Model, Session, Tool, ToolHook, ToolSet

from wy_coding_agent.anthropic import AnthropicModel
from wy_coding_agent.config import (
    CompactionConfig,
    ConfigError,
    load_bash_config,
    load_compaction_config,
    load_config,
    load_mcp_config,
    load_skills_config,
)
from wy_coding_agent.conversation import ConversationService
from wy_coding_agent.mcp import MCPClientManager
from wy_coding_agent.prompt_template import (
    BasePromptProvider,
    build_prompt,
    build_prompt_context,
    default_instruction_providers,
    default_prompt_providers,
)
from wy_coding_agent.reminders import ClaudeMdReminderProvider, HarnessState
from wy_coding_agent.session import SessionStore
from wy_coding_agent.skills import default_skills_dirs, discover_skills
from wy_coding_agent.tools import DEFAULT_TOOLS
from wy_coding_agent.tools.agent import AgentTool
from wy_coding_agent.tools.bash import BASH
from wy_coding_agent.tools.bash_policy import build_policy
from wy_coding_agent.tools.plan import ExitPlanModeTool
from wy_coding_agent.tools.tool_search import ToolSearchTool
from wy_coding_agent.skills import SkillTool
from wy_coding_agent.tool_policy import WorkspaceToolHook


def bootstrap(
    session_id: str | None = None,
    *,
    config_path: Path | None = None,
) -> ConversationService:
    """One-stop assembly: read config.toml and return a ready-to-use agent.

    config_path 缺省为调用方 CWD 的 config.toml，显式传参可覆盖。同一份文件
    提供 [anthropic]（模型构造参数，必填段）、[compaction]（可选）、[skills]
    （可选）、[bash]（可选，命令分级规则与默认超时，经 `BASH.configure` 装到
    进程级的 bash 工具实例上）与 [[mcp.servers]]（可选）配置。组装内容：
    `load_config` 构造 `AnthropicModel`、内置工具集 `DEFAULT_TOOLS`、
    `load_compaction_config` 的压缩参数，最终交给 `create_agent`
    （mcp_config 即同一配置文件）。
    skills 目录取 `load_skills_config`；未配置时用 `default_skills_dirs()`
    的官方默认（`~/.claude/skills` 优先于 `CWD/.claude/skills`），
    `dirs = []` 显式关闭。session_id 语义同 `create_agent`。配置/连接失败抛
    ConfigError/ModelError/SessionError，由调用方处理。
    需要自定义模型、工具集或提示词组合时，直接使用 `create_agent`。
    """
    config_path = config_path if config_path is not None else Path.cwd() / "config.toml"
    config = load_config(config_path)
    model = AnthropicModel(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        max_tokens=config.max_tokens,
        thinking_budget_tokens=config.thinking_budget_tokens,
    )
    # bash 分级策略是进程级的(BASH 是 DEFAULT_TOOLS 里的单例),在这里配置:
    # create_agent 不读配置文件,策略只能由显式读配置的 bootstrap 装上。
    bash_config = load_bash_config(config_path)
    BASH.configure(
        build_policy(allow=bash_config.allow, deny=bash_config.deny),
        timeout=bash_config.timeout,
    )
    skills_dirs = load_skills_config(config_path)
    return create_agent(
        session_id,
        model=model,
        tools=DEFAULT_TOOLS,
        compaction=load_compaction_config(config_path),
        mcp_config=config_path,
        skills_dirs=skills_dirs if skills_dirs is not None else default_skills_dirs(),
    )


def create_agent(
    session_id: str | None = None,
    *,
    model: Model,
    instruction_providers: Sequence[BasePromptProvider] | None = None,
    tools: Sequence[Tool] | None = None,
    prompt_providers: Sequence[BasePromptProvider] | None = None,
    workspace: Path | None = None,
    sessions_dir: Path | None = None,
    compaction: CompactionConfig | None = None,
    mcp_config: Path | None = None,
    skills_dirs: Sequence[Path] | None = None,
    audit: bool = True,
    tool_hook: ToolHook | None = None,
) -> ConversationService:
    """Create an agent backed by a durable session.

    传入 session_id 时恢复既有会话（完成的问答对回灌 wy-core 会话，累计
    用量、上下文规模与最近一条状态快照——如 plan 模式——一并恢复）；
    省略时自动生成 UUID 新会话。model 必填：
    宿主按 `wy_core.Model` 契约实现后注入，厂商参数在构造期给定。
    tools 按 `wy_core.Tool` 契约编写后传入，省略时无本地工具（仅配置的
    MCP 工具可用）；名为 `agent` 的子 agent 派生工具总是额外装配（复用
    同一 model 与既有工具集，见 `tools/agent.py`），`exit_plan_mode`
    工具与 harness 状态同样总是装配（`/plan` 交互见 TUI；plan 是 harness
    状态驱动的 system 分节——system 由 `system_builder` 在每次提交 LLM 时按
    harness 状态实时组装，返回值的 `plan_mode` 属性供宿主经 `set_plan_mode`
    切换）。claudeMd 上下文（全局/项目指令 + 记忆 + 日期）经
    `ClaudeMdReminderProvider` 每回合注入 `<system-reminder>`（reminders 层）。
    工具集里凡 `Tool.deferred` 为真的（MCP 工具默认如此）都是懒加载：
    不随请求发送，只有名字进 system prompt，模型经额外装配的 `tool_search`
    工具按需加载（存在懒加载工具时才装配，见 `tools/tool_search.py`）。
    prompt_providers 省略时使用
    default_prompt_providers(skills, deferred_names) 的默认
    动态组合；instruction_providers 省略时使用
    default_instruction_providers(context) 的五段静态指令（身份/Harness/会话
    指引/环境/上下文管理，环境事实经占位符注入），传入则整体替换静态段。
    prompt_providers 非 None 时整条链（含静态段）以它为准，instruction_providers
    被忽略。sessions_dir 省略时写入当前目录 .agent_session/。mcp_config 为 MCP
    配置文件路径（TOML 的 [[mcp.servers]] 段），None 即不启用 MCP，传入时
    文件必须存在且合法；其中的 server 工具以 mcp__<server>__<tool> 名称
    并入工具集，连接失败的 server 记 warning 并跳过。skills_dirs 为
    Agent Skills 目录列表（每个子目录一个 `<name>/SKILL.md`，顺序即优先
    级、同名先者胜），None 即不启用 skills（与 mcp_config 同语义，官方
    默认目录见 `default_skills_dirs()`）；发现的 skills 经 `skill` 工具
    按需加载、清单由 SkillProvider 注入系统提示。compaction 控制
    wy-core 的自动上下文压缩阈值，None 即默认值。审计日志默认写入会话文件
    同目录的 <session_id>.audit.jsonl，audit=False 关闭。
    调用方结束后应调用返回值的 close() 释放连接。
    """
    compaction = compaction if compaction is not None else CompactionConfig()
    mcp_servers = load_mcp_config(mcp_config) if mcp_config is not None else ()
    skills = discover_skills(skills_dirs) if skills_dirs is not None else ()

    store = SessionStore(session_id, sessions_dir=sessions_dir)
    base_tools = () if tools is None else tuple(tools)
    mcp_manager: MCPClientManager | None = None
    if mcp_servers:
        mcp_manager = MCPClientManager(mcp_servers)
        mcp_manager.start()
    all_tools = base_tools + (mcp_manager.tools if mcp_manager else ())
    if skills:
        all_tools = all_tools + (SkillTool(skills),)
    # 懒加载工具只有名字进 system prompt(schema 经 tool_search 按需取回);
    # 下面追加的 agent/exit_plan_mode/tool_search 都是直接加载,不进清单。
    deferred_names = tuple(tool.name for tool in all_tools if tool.deferred)
    context = build_prompt_context(model.name, workspace)
    if prompt_providers is not None:
        providers = tuple(prompt_providers)
    else:
        static = (
            default_instruction_providers(context)
            if instruction_providers is None
            else tuple(instruction_providers)
        )
        dynamic = default_prompt_providers(skills, deferred_names)
        providers = (*static, *dynamic)
    # system 由 system_builder 在每次提交 LLM 时按 harness 状态组装(plan 激活
    # 追加 # Plan mode 段);base_system 是 plan-off 快照,给子 agent 用。
    harness = HarnessState()
    base_system = build_prompt(providers)
    system_builder = lambda: build_prompt(providers, harness=harness)
    # agent 工具持有追加前的工具集快照:子 agent 不含 agent 工具,杜绝嵌套派生。
    # exit_plan_mode 同样在快照之后追加:plan 模式属主会话状态,子 agent 不可控制。
    all_tools = all_tools + (
        AgentTool(
            model=model,
            tools=all_tools,
            system=base_system,
            audit_base=store.path.with_suffix("") if audit else None,
            max_context_tokens=compaction.max_context_tokens,
            keep_recent=compaction.keep_recent,
        ),
        ExitPlanModeTool(harness),
    )
    names = [tool.name for tool in all_tools]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        if mcp_manager is not None:
            mcp_manager.close()
        raise ConfigError(f"工具名冲突：{sorted(duplicates)}")

    # 工具列表按需收缩:懒加载工具不随请求发送,由 tool_search 加载进来。
    # 没有懒加载工具就不装搜索工具,避免多一个无用工具占上下文。
    toolset = ToolSet(all_tools)
    if toolset.deferred:
        toolset.add(ToolSearchTool(toolset))

    session = Session(
        max_context_tokens=compaction.max_context_tokens,
        keep_recent=compaction.keep_recent,
    )
    for message in store.conversation_messages():
        session.append(message)
    session.total_usage = store.total_usage
    session.context_tokens = store.last_context_tokens
    state = AgentState(session=session, extensions=(harness,))
    latest_state = store.latest_state()
    if latest_state is not None:
        state.restore(dict(latest_state))

    if tool_hook is None:
        tool_hook = WorkspaceToolHook(
            workspace or Path.cwd(),
            tools={t.name: t for t in toolset.all},
        )

    try:
        agent = Agent(
            model=model,
            tools=toolset,
            system=base_system,
            system_builder=system_builder,
            state=state,
            audit=(
                AuditLog(store.path.with_suffix(".audit.jsonl")) if audit else None
            ),
            tool_hook=tool_hook,
        )
    except BaseException:
        if mcp_manager is not None:
            mcp_manager.close()
        raise
    claude_md = ClaudeMdReminderProvider(workspace=context.workspace)
    return ConversationService(
        agent,
        store,
        closer=mcp_manager.close if mcp_manager is not None else None,
        reminder_providers=(claude_md,),
    )

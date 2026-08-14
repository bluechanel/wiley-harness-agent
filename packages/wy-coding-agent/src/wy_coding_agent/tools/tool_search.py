"""工具搜索执行器:把懒加载(``Tool.deferred``)工具按需加载进工具列表。

与 ``tools/agent.py``、``tools/plan.py`` 同型:本模块只有类、无模块级实例,
自动扫描不收录;由 factory(主 agent)与 ``AgentTool``(每次派生)各自
构造,各持一份 ``wy_core.ToolSet`` ——激活状态因此天然隔离,且只存内存,
不随会话持久化。

模型侧的用法对齐 Claude Code 的 ToolSearchTool:懒加载工具只有名字进
system prompt(schema 不随请求发送,故不可直接调用),模型用关键词或
``select:<name>`` 搜到后,本工具返回它们的完整 schema 并把它们加入
后续请求的工具列表。
"""

import json
import re
from collections.abc import Iterable, Sequence

from wy_core import Tool, ToolSet

_MAX_LISTED_DEFERRED = 30  # 无命中时最多回列多少个待加载工具名


class ToolSearchTool(Tool):
    """Find deferred tools and load their schemas so they can be called."""

    name = "tool_search"
    description = (
        "Fetch full schema definitions for deferred tools so they can be "
        "called.\n"
        "Deferred tools are listed by name in the system prompt. Until "
        "fetched, only the name is known — there is no parameter schema, so "
        "the tool cannot be invoked. This tool takes a query, matches it "
        "against the deferred tool list, and returns the matched tools' "
        "complete JSON Schema definitions inside a <functions> block. Once a "
        "tool's schema appears in that result, it is callable exactly like "
        "any other tool.\n"
        "Query forms:\n"
        '- "select:Read,Edit,Grep" — fetch these exact tools by name\n'
        '- "notebook jupyter" — keyword search, up to max_results best '
        "matches\n"
        '- "+slack send" — require "slack" in the name, rank by remaining '
        "terms"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    'Query to find deferred tools. Use "select:<tool_name>" '
                    "for direct selection, or keywords to search."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, toolset: ToolSet) -> None:
        self._toolset = toolset

    def execute(self, input: dict) -> str:
        query = str(input.get("query", "")).strip()
        if not query:
            raise ValueError("query is empty; pass keywords or select:<tool_name>")
        max_results = _max_results(input.get("max_results"))

        deferred = self._toolset.deferred
        select = re.match(r"^select:(.+)$", query, re.IGNORECASE)
        if select is not None:
            matches = self._select(select.group(1))
        else:
            matches = _search_by_keywords(query, deferred, self._toolset, max_results)

        if not matches:
            return _no_match_text(query, deferred)
        self._toolset.activate(*(tool.name for tool in matches))
        return _render(matches)

    def _select(self, raw: str) -> tuple[Tool, ...]:
        """``select:A,B,C`` 直选:逐名取工具,未知名忽略。

        名字不在待加载集合但已在工具列表里时照样返回——重复"加载"是无害
        的幂等操作,免得模型为此空转重试。
        """
        found: list[Tool] = []
        seen: set[str] = set()
        for name in (part.strip() for part in raw.split(",")):
            if not name:
                continue
            tool = self._toolset.get(name)
            if tool is None or tool.name in seen:
                continue
            seen.add(tool.name)
            found.append(tool)
        return tuple(found)


def _max_results(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 5
    return value if value > 0 else 5


def _parse_tool_name(name: str) -> tuple[list[str], str, bool]:
    """把工具名切成可搜索的词:MCP 名按 ``__``/``_``,普通名按 CamelCase。

    返回 (词列表, 全名文本, 是否 MCP 工具)。
    """
    if name.startswith("mcp__"):
        without_prefix = name[len("mcp__") :].lower()
        parts = [
            piece
            for chunk in without_prefix.split("__")
            for piece in chunk.split("_")
            if piece
        ]
        return parts, without_prefix.replace("__", " ").replace("_", " "), True

    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ").lower()
    parts = [piece for piece in spaced.split() if piece]
    return parts, " ".join(parts), False


def _compile_terms(terms: Iterable[str]) -> dict[str, re.Pattern[str]]:
    """预编译各搜索词的词边界正则(每次搜索只编一次)。"""
    return {term: re.compile(rf"\b{re.escape(term)}\b") for term in terms}


def _search_by_keywords(
    query: str,
    deferred: Sequence[Tool],
    toolset: ToolSet,
    max_results: int,
) -> tuple[Tool, ...]:
    """按名字与描述打分检索待加载工具(移植自 Claude Code 的 ToolSearchTool)。"""
    query_lower = query.lower().strip()

    # 快路径:查询恰好是一个工具名(模型有时不带 select: 前缀直接给名字)。
    # 先看待加载集合,再回落全量——已加载的工具"再选一次"是无害的幂等操作。
    for tool in (*deferred, *toolset.all):
        if tool.name.lower() == query_lower:
            return (tool,)

    # 查询形如 mcp__server:按前缀匹配(模型常直接用 server 名搜)。
    if query_lower.startswith("mcp__") and len(query_lower) > 5:
        prefixed = tuple(
            tool for tool in deferred if tool.name.lower().startswith(query_lower)
        )[:max_results]
        if prefixed:
            return prefixed

    query_terms = [term for term in query_lower.split() if term]
    required = [term[1:] for term in query_terms if term.startswith("+") and len(term) > 1]
    optional = [term for term in query_terms if not term.startswith("+") or len(term) == 1]
    scoring_terms = [*required, *optional] if required else query_terms
    patterns = _compile_terms(scoring_terms)

    # 必含词(+前缀)先做预筛:名字或描述里必须全部出现。
    candidates = deferred
    if required:
        candidates = tuple(
            tool
            for tool in deferred
            if _matches_all(tool, required, patterns)
        )

    scored: list[tuple[int, int, Tool]] = []
    for index, tool in enumerate(candidates):
        parts, full, is_mcp = _parse_tool_name(tool.name)
        description = tool.description.lower()
        score = 0
        for term in scoring_terms:
            pattern = patterns[term]
            if term in parts:
                score += 12 if is_mcp else 10
            elif any(term in part for part in parts):
                score += 6 if is_mcp else 5
            if term in full and score == 0:
                score += 3
            if pattern.search(description):
                score += 2
        if score > 0:
            scored.append((-score, index, tool))  # index 保证同分稳定排序

    scored.sort(key=lambda item: (item[0], item[1]))
    return tuple(tool for _, _, tool in scored[:max_results])


def _matches_all(
    tool: Tool, terms: Sequence[str], patterns: dict[str, re.Pattern[str]]
) -> bool:
    parts, _, _ = _parse_tool_name(tool.name)
    description = tool.description.lower()
    return all(
        term in parts
        or any(term in part for part in parts)
        or patterns[term].search(description) is not None
        for term in terms
    )


def _render(tools: Sequence[Tool]) -> str:
    """把命中工具渲染成完整 schema 文本(与 system prompt 的工具定义同形)。"""
    lines = [
        "以下工具已加载,可以像其他工具一样直接调用:",
        "",
        "<functions>",
    ]
    for tool in tools:
        definition = {
            "description": tool.description,
            "name": tool.name,
            "parameters": tool.parameters,
        }
        lines.append(f"<function>{json.dumps(definition, ensure_ascii=False)}</function>")
    lines.append("</functions>")
    return "\n".join(lines)


def _no_match_text(query: str, deferred: Sequence[Tool]) -> str:
    if not deferred:
        return f"No matching deferred tools found for {query!r}; 当前没有待加载的工具。"
    if len(deferred) <= _MAX_LISTED_DEFERRED:
        names = ", ".join(tool.name for tool in deferred)
        return (
            f"No matching deferred tools found for {query!r}. "
            f"当前待加载的工具:{names}"
        )
    return (
        f"No matching deferred tools found for {query!r}. "
        f"当前还有 {len(deferred)} 个待加载工具,换个关键词再试。"
    )

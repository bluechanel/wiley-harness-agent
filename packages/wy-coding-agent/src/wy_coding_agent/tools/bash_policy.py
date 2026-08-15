"""bash 命令分级策略：把一条命令判成 allow / ask / deny 三档。

**这是分诊与降噪，不是安全边界。** 静态分析 bash 不可能完备——变量展开、
别名、命令替换、脚本内容都能绕过任何静态规则。真正的边界是用户审批：
``ask`` 会弹到用户面前由人拍板，``deny`` 是连审批机会都不给的硬拒绝，
``allow`` 只用来给高频只读命令免打扰。无沙箱，命令以当前用户权限运行。

分级顺序即优先级（见 ``classify``）：deny 规则 → 静态分析失效/有副作用
（永不放行）→ allow 规则 → 兜底 ask。第三档在第二档之后，因此配置里再宽
的 allow 规则也盖不过命令替换、写入重定向与敏感文件。

本模块是纯函数 + 数据类，无 I/O、无模块级 Tool 实例（``DEFAULT_TOOLS``
的扫描自然跳过）；``Tool.approve()`` 一次审批会被调用多次，这里的判定
必须便宜。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "ask", "deny"]


class BashPolicyError(ValueError):
    """命令无法被切分（未闭合引号等）。"""


# ── 内置规则 ────────────────────────────────────────────────
#
# 规则语法对齐 Claude Code 的 Bash(git diff:*)：``"git diff:*"`` 是 token
# 前缀匹配，``"pwd"`` 是整条精确匹配。

DEFAULT_ALLOW: tuple[str, ...] = (
    "ls:*",
    "pwd",
    "echo:*",
    "cat:*",
    "head:*",
    "tail:*",
    "wc:*",
    "file:*",
    "which:*",
    "date",
    "uname:*",
    "rg:*",
    "grep:*",
    "git status:*",
    "git diff:*",
    "git log:*",
    "git show:*",
    "git branch",
)
"""默认放行的只读命令。

刻意不含 ``find``（``-exec``/``-delete`` 能删文件）、``env``（打印密钥）、
``curl``/``wget``（把工作区内容外发）。执行仓库内代码的命令——
``uv run pytest`` 之类——同样不进默认：工作区内的 write/edit 是自动放行的，
"写测试文件 + 自动跑测试"等于自动执行任意代码。这类规则由每个项目在
config.toml 的 ``[bash] allow`` 里显式打开。
"""

DEFAULT_DENY: tuple[str, ...] = (
    "sudo:*",
    "su:*",
    "shutdown:*",
    "reboot:*",
    "halt:*",
    "mkfs:*",
)
"""默认硬拒绝的命令。保持极小：审批疲劳会让用户闭眼点"是"，所以只有
"批准了也不该执行"的才进这里，其余一律交给用户审批。"""

# 参数决定实际执行内容的命令：即使命中 allow 规则也强制审批，
# 因为真正要跑的东西不在静态可见的 token 里。
_COMMAND_RUNNERS = frozenset(
    {
        "xargs", "env", "eval", "exec", "source", ".", "nohup", "time",
        "timeout", "watch", "sh", "bash", "zsh", "dash", "fish",
        "python", "python3", "node", "ruby", "perl", "ssh",
    }
)

# 会产生副作用的参数：命令本身像只读，加上这些参数就不是了。
_UNSAFE_FLAGS: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls", "-fprint"}),
}

# 命中即强制审批的路径片段：泄密比误伤更贵。本仓 config.toml 里就有真实的
# anthropic.api_key，`cat config.toml` 会把它直接送进模型上下文。
_SENSITIVE = (
    ".env", "config.toml", "id_rsa", "id_ed25519", ".ssh/", ".aws/",
    ".netrc", "credentials", "secret",
)

# rm -rf 指到这些目标就是灾难，不给审批机会。
_ROOT_TARGETS = frozenset(
    {
        "/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}", "$HOME/", "$HOME/*",
        "/usr", "/etc", "/var", "/bin", "/sbin", "/lib", "/opt", "/private",
        "/System", "/Library", "/Applications", "/Users",
    }
)
_RECURSIVE_FLAGS = frozenset({"-r", "-R", "-rf", "-fr", "-Rf", "-fR", "--recursive"})

_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "dash", "fish", "python", "python3"})

# 命令替换与进程替换：静态分析到此为止。$((...)) 也会命中，保守即可。
_SUBSTITUTION_RE = re.compile(r"\$\(|`|<\(|>\(")
# fork bomb :(){ :|:& };:
_FORK_BOMB_RE = re.compile(r":\s*\(\s*\)\s*\{")


@dataclass(frozen=True, slots=True)
class BashPolicy:
    """一份分级规则。默认即内置规则；配置文件的规则经 ``build_policy`` 追加。"""

    allow: tuple[str, ...] = DEFAULT_ALLOW
    deny: tuple[str, ...] = DEFAULT_DENY


@dataclass(frozen=True, slots=True)
class Verdict:
    """分级结果。``reason`` 同时给用户（审批卡片）和模型（拒绝文本）看。"""

    decision: Decision
    reason: str


@dataclass(frozen=True, slots=True)
class SubCommand:
    """复合命令切分出的一段。

    - ``tokens`` 该段的词（引号已剥离，重定向算子与其目标已剔除）
    - ``piped_input`` 是否由管道喂入（``curl x | sh`` 的 ``sh``）
    - ``writes`` 写入重定向的目标（``/dev/null`` 与 fd 号已剔除）
    """

    tokens: tuple[str, ...]
    piped_input: bool = False
    writes: tuple[str, ...] = ()


def build_policy(
    allow: Sequence[str] = (), deny: Sequence[str] = ()
) -> BashPolicy:
    """在内置规则之上追加配置规则——配置是扩展，不是替换。"""
    return BashPolicy(
        allow=DEFAULT_ALLOW + tuple(allow),
        deny=DEFAULT_DENY + tuple(deny),
    )


# ── 切分 ────────────────────────────────────────────────────


def _tokenize(command: str) -> list[str]:
    """切词，shell 算子成为独立 token。

    ``punctuation_chars=True`` 让 ``();<>|&`` 成组产出（``&&``、``>>``、
    ``2>&1`` → ``['2', '>&', '1']``）；``commenters=""`` 让 ``#`` 保持普通
    字符——按注释截断会留下分析盲区，多出来的 token 顶多让判定落到 ask。
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as exc:
        raise BashPolicyError(str(exc)) from exc


def _is_punctuation(token: str) -> bool:
    return bool(token) and all(char in "();<>|&" for char in token)


def split_commands(command: str) -> tuple[SubCommand, ...]:
    """把复合命令切成子命令，供逐段分级。

    按控制算子（``&&`` ``||`` ``|`` ``;`` ``&`` ``(`` ``)``）切段；重定向
    算子及其目标不进 token，只在 ``writes`` 里留下写入目标。
    """
    tokens = _tokenize(command)
    subs: list[SubCommand] = []
    current: list[str] = []
    writes: list[str] = []
    piped = False
    index = 0

    def flush() -> None:
        if current:
            subs.append(
                SubCommand(tokens=tuple(current), piped_input=piped, writes=tuple(writes))
            )
        current.clear()
        writes.clear()

    while index < len(tokens):
        token = tokens[index]
        if not _is_punctuation(token):
            current.append(token)
            index += 1
            continue
        if "<" in token or ">" in token:  # 重定向：算子与目标都不算命令词
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
            if ">" in token and not _is_harmless_target(target):
                writes.append(target)
            index += 2
            continue
        flush()  # 控制算子：本段结束
        piped = token in ("|", "|&")
        index += 1

    flush()
    return tuple(subs)


def _is_harmless_target(target: str) -> bool:
    """重定向到 /dev/null 或某个 fd（``2>&1``）不算写副作用。"""
    return target == "" or target == "/dev/null" or target.isdigit()


# ── 规则匹配 ────────────────────────────────────────────────


def match_rule(rule: str, tokens: Sequence[str]) -> bool:
    """``"git diff:*"`` 前缀匹配；``"pwd"`` 整条精确匹配。"""
    prefix_mode = rule.endswith(":*")
    parts = tuple((rule[:-2] if prefix_mode else rule).split())
    if not parts:
        return False
    if prefix_mode:
        return tuple(tokens[: len(parts)]) == parts
    return tuple(tokens) == parts


def _deny_reason(sub: SubCommand, policy: BashPolicy) -> str | None:
    for rule in policy.deny:
        if match_rule(rule, sub.tokens):
            return f"命中拒绝规则 '{rule}'"
    executable = sub.tokens[0]
    if sub.piped_input and executable in _SHELL_INTERPRETERS:
        return "管道直接喂给 shell 解释器执行"
    if executable == "rm" and _rm_targets_root(sub.tokens):
        return "rm 递归删除指向根目录或家目录"
    if executable == "dd" and any(token.startswith("of=/dev/") for token in sub.tokens):
        return "dd 直接写入块设备"
    return None


def _rm_targets_root(tokens: Sequence[str]) -> bool:
    if not any(token in _RECURSIVE_FLAGS for token in tokens):
        return False
    return any(token in _ROOT_TARGETS for token in tokens[1:])


def _never_allow_reason(command: str, subs: Sequence[SubCommand]) -> str | None:
    """静态分析失效或命令有副作用时的原因；这一档 allow 规则盖不过。"""
    if _SUBSTITUTION_RE.search(command):
        return "含命令替换/进程替换，无法静态判定实际执行内容"
    for sub in subs:
        if sub.writes:
            return f"写入重定向：{' '.join(sub.writes)}"
        hit = _sensitive_hit(sub.tokens)
        if hit is not None:
            return f"涉及敏感文件：{hit}"
        executable = sub.tokens[0]
        if executable in _COMMAND_RUNNERS:
            return f"'{executable}' 执行的内容由参数决定，静态不可见"
        unsafe = sorted(_UNSAFE_FLAGS.get(executable, frozenset()).intersection(sub.tokens[1:]))
        if unsafe:
            return f"'{executable}' 带有副作用参数 {' '.join(unsafe)}"
    return None


def _sensitive_hit(tokens: Sequence[str]) -> str | None:
    for token in tokens[1:]:
        for needle in _SENSITIVE:
            if needle in token:
                return token
    return None


def _allow_rule(sub: SubCommand, policy: BashPolicy) -> str | None:
    for rule in policy.allow:
        if match_rule(rule, sub.tokens):
            return rule
    return None


# ── 入口 ────────────────────────────────────────────────────


def classify(command: str, policy: BashPolicy | None = None) -> Verdict:
    """给一条命令定级。复合命令取最严：任一段该拒就拒，全部该放行才放行。"""
    policy = policy if policy is not None else BashPolicy()

    if _FORK_BOMB_RE.search(command):
        return Verdict("deny", "疑似 fork bomb")

    try:
        subs = split_commands(command)
    except BashPolicyError as exc:
        return Verdict("ask", f"命令无法静态解析（{exc}）")
    if not subs:
        return Verdict("ask", "空命令")

    for sub in subs:
        reason = _deny_reason(sub, policy)
        if reason is not None:
            return Verdict("deny", reason)

    blocker = _never_allow_reason(command, subs)
    if blocker is not None:
        return Verdict("ask", blocker)

    matched: list[str] = []
    for sub in subs:
        rule = _allow_rule(sub, policy)
        if rule is None:
            return Verdict("ask", f"未匹配任何允许规则：{' '.join(sub.tokens[:3])}")
        matched.append(rule)
    rules = "、".join(f"'{rule}'" for rule in dict.fromkeys(matched))
    return Verdict("allow", f"匹配允许规则 {rules}")

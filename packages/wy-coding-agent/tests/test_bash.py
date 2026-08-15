import os
import threading
from pathlib import Path

import pytest

from wy_coding_agent.tools import bash as bash_module
from wy_coding_agent.tools.bash import BASH, BashToolError
from wy_coding_agent.tools.bash_policy import BashPolicy


@pytest.fixture(autouse=True)
def fresh_session():
    BASH._reset_session()
    BASH.configure()  # 内置策略 + 默认超时
    yield
    BASH._reset_session()
    BASH.configure()


# ── 入参校验 ────────────────────────────────────────────────


def test_missing_command_is_rejected() -> None:
    with pytest.raises(BashToolError, match="Missing required argument: command"):
        BASH.execute({})


def test_blank_command_is_rejected() -> None:
    with pytest.raises(BashToolError, match="Empty command"):
        BASH.execute({"command": "   "})


def test_unterminated_quote_is_rejected_before_running() -> None:
    """未闭合引号会吞掉哨兵行挂到超时，语法预检提前拦下。"""
    with pytest.raises(BashToolError, match="syntax error"):
        BASH.execute({"command": "echo 'unterminated"})


# ── 分级接线 ────────────────────────────────────────────────


def test_readonly_command_needs_no_approval() -> None:
    assert BASH.approve({"command": "git status"}, Path.cwd()) is None


def test_side_effecting_command_requests_approval() -> None:
    request = BASH.approve({"command": "mkdir data"}, Path.cwd())

    assert request is not None
    assert ("命令", "mkdir data") in request.fields
    assert request.key == "bash:mkdir data"
    assert any(label == "原因" for label, _ in request.fields)


def test_denied_command_raises_from_approve() -> None:
    """审批阶段抛异常即硬拒绝——hook 会把它变成拒绝，用户不会被问。"""
    with pytest.raises(BashToolError, match="策略拒绝"):
        BASH.approve({"command": "sudo rm -rf /"}, Path.cwd())


def test_denied_command_is_refused_without_running(tmp_path: Path) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("hi")

    with pytest.raises(BashToolError, match="策略拒绝"):
        BASH.execute({"command": f"sudo rm '{target}'"})

    assert target.exists()  # 没有 hook 时执行层也要拦住


def test_configure_installs_policy() -> None:
    assert BASH.approve({"command": "mkdir data"}, Path.cwd()) is not None

    BASH.configure(BashPolicy(allow=("mkdir:*",)))

    assert BASH.approve({"command": "mkdir data"}, Path.cwd()) is None


# ── 执行 ────────────────────────────────────────────────────


def test_echo_returns_output() -> None:
    assert BASH.execute({"command": "echo hello"}) == "hello"


def test_stderr_is_interleaved_with_stdout() -> None:
    assert BASH.execute({"command": "echo err >&2"}) == "err"


def test_silent_success_reports_no_output() -> None:
    assert BASH.execute({"command": "cat /dev/null"}) == "(no output)"


def test_nonzero_exit_code_is_reported() -> None:
    assert BASH.execute({"command": "grep needle /dev/null"}) == "Exit code: 1"


def test_command_outside_the_allowlist_still_runs() -> None:
    """执行层只拦 deny：ask 档由审批层把关，不在这里二次拒绝。"""
    assert BASH.execute({"command": "python3 -c 'print(7 * 6)'"}) == "42"


def test_stdin_is_closed_so_interactive_commands_do_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdin 接 /dev/null：等输入的命令立刻结束，而不是耗尽超时并连坐杀会话。"""
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 5)

    assert BASH.execute({"command": "cat"}) == "(no output)"
    assert BASH.execute({"command": "echo still-alive"}) == "still-alive"


def test_working_directory_persists_between_commands(tmp_path: Path) -> None:
    BASH.execute({"command": f"cd '{tmp_path}'"})

    assert BASH.execute({"command": "pwd -P"}) == os.path.realpath(tmp_path)


def test_restart_clears_state(tmp_path: Path) -> None:
    BASH.execute({"command": f"cd '{tmp_path}'"})

    assert BASH.execute({"restart": True}) == "Bash session restarted"
    assert BASH.execute({"command": "pwd -P"}) == os.path.realpath(os.getcwd())


def test_concurrent_calls_do_not_interleave() -> None:
    """一个 shell 一次只能跑一条命令：并发调用必须各自拿回自己的输出。"""
    results: dict[str, str] = {}

    def run(tag: str) -> None:
        results[tag] = BASH.execute({"command": f"sleep 0.2; echo {tag}"})

    threads = [threading.Thread(target=run, args=(tag,)) for tag in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert results == {"alpha": "alpha", "beta": "beta"}


# ── 输出截断 ────────────────────────────────────────────────


def test_large_output_keeps_head_and_tail() -> None:
    """结论在尾部（测试摘要、编译错误），所以截中间而不是截尾。"""
    output = BASH.execute({"command": "seq 500"})

    assert "lines omitted (500 total lines)" in output
    assert output.startswith("1\n2\n")
    assert output.rstrip().endswith("\n500")
    assert "\n200\n" not in output  # 中间那段被省略


def test_invalid_utf8_output_does_not_break_the_session() -> None:
    output = BASH.execute({"command": "printf '\\xff\\xfe end'"})

    assert output.endswith("end")
    assert BASH.execute({"command": "echo still-alive"}) == "still-alive"


# ── 超时与会话恢复 ──────────────────────────────────────────


def test_timeout_kills_command_and_restarts_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 1)

    with pytest.raises(BashToolError, match="did not finish within 1 seconds"):
        BASH.execute({"command": "sleep 5"})

    assert BASH.execute({"command": "echo recovered"}) == "recovered"


def test_timeout_reports_partial_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 2)

    with pytest.raises(BashToolError, match="printed-before-hang"):
        BASH.execute({"command": "/bin/echo printed-before-hang; sleep 9"})


def test_timeout_preserves_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次超时不该赔上工作目录：重建的会话回到原来的 cwd。"""
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 1)
    BASH.execute({"command": f"cd '{tmp_path}'"})

    with pytest.raises(BashToolError, match="did not finish"):
        BASH.execute({"command": "sleep 5"})

    assert BASH.execute({"command": "pwd -P"}) == os.path.realpath(tmp_path)


def test_timeout_argument_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 60)

    with pytest.raises(BashToolError, match="did not finish within 1 seconds"):
        BASH.execute({"command": "sleep 5", "timeout": 1})


def test_timeout_argument_is_validated() -> None:
    with pytest.raises(BashToolError, match="positive integer"):
        BASH.execute({"command": "echo hi", "timeout": 0})


def test_timeout_argument_is_capped() -> None:
    assert BASH._resolve_timeout(10_000) == bash_module._MAX_TIMEOUT_SECONDS


def test_configured_timeout_is_used_when_no_argument() -> None:
    BASH.configure(timeout=1)

    with pytest.raises(BashToolError, match="did not finish within 1 seconds"):
        BASH.execute({"command": "sleep 5"})


def test_session_exit_is_reported_and_recovered() -> None:
    with pytest.raises(BashToolError, match="session exited"):
        BASH.execute({"command": "exit"})

    assert BASH.execute({"command": "echo recovered"}) == "recovered"

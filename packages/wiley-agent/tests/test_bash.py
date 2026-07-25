import os
from collections.abc import Callable
from pathlib import Path

import pytest

from wiley_agent.tools import bash as bash_module
from wiley_agent.tools.bash import (
    BashToolError,
    execute_bash,
    validate_command,
)


@pytest.fixture(autouse=True)
def fresh_session():
    bash_module._reset_session()
    yield
    bash_module._reset_session()


@pytest.fixture
def allow_extra(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Extend the command allowlist for tests that exercise session behavior."""

    def allow(*commands: str) -> None:
        monkeypatch.setattr(
            bash_module,
            "ALLOWED_COMMANDS",
            bash_module.ALLOWED_COMMANDS | set(commands),
        )

    return allow


def test_validate_allows_allowlisted_command() -> None:
    assert validate_command("grep -n pattern file.txt") == (True, None)


def test_validate_rejects_command_outside_allowlist() -> None:
    allowed, reason = validate_command("rm -rf /tmp/x")

    assert not allowed
    assert reason == "Command 'rm' is not in the allowlist"


def test_validate_rejects_standalone_shell_operators() -> None:
    allowed, reason = validate_command("cat a.txt | grep x")

    assert not allowed
    assert reason == "Shell operator '|' is not allowed"


def test_validate_rejects_expansions() -> None:
    for command in ("echo $(pwd)", "echo $HOME", "echo `pwd`"):
        allowed, _ = validate_command(command)
        assert not allowed


def test_validate_rejects_unparseable_command() -> None:
    assert validate_command("echo 'unterminated") == (False, "Could not parse command")


def test_validate_rejects_empty_command() -> None:
    assert validate_command("   ") == (False, "Empty command")


def test_execute_rejects_disallowed_command_without_running_it() -> None:
    with pytest.raises(BashToolError, match="not in the allowlist"):
        execute_bash({"command": "python -c 'print(1)'"})


def test_missing_command_is_rejected() -> None:
    with pytest.raises(BashToolError, match="Missing required argument: command"):
        execute_bash({})


def test_echo_returns_output() -> None:
    assert execute_bash({"command": "echo hello"}) == "hello"


def test_stderr_is_interleaved_with_stdout() -> None:
    assert execute_bash({"command": "echo err >&2"}) == "err"


def test_silent_success_reports_no_output() -> None:
    assert execute_bash({"command": "cat /dev/null"}) == "(no output)"


def test_nonzero_exit_code_is_reported() -> None:
    assert execute_bash({"command": "grep needle /dev/null"}) == "Exit code: 1"


def test_working_directory_persists_between_commands(
    tmp_path: Path, allow_extra: Callable[..., None]
) -> None:
    allow_extra("cd")

    execute_bash({"command": f"cd '{tmp_path}'"})

    assert execute_bash({"command": "pwd -P"}) == os.path.realpath(tmp_path)


def test_restart_clears_state(
    tmp_path: Path, allow_extra: Callable[..., None]
) -> None:
    allow_extra("cd")
    execute_bash({"command": f"cd '{tmp_path}'"})

    assert execute_bash({"restart": True}) == "Bash session restarted"
    assert execute_bash({"command": "pwd -P"}) == os.path.realpath(os.getcwd())


def test_large_output_is_truncated(allow_extra: Callable[..., None]) -> None:
    allow_extra("seq")

    output = execute_bash({"command": "seq 500"})

    assert "Output truncated (500 total lines)" in output
    assert "\n201\n" not in output


def test_timeout_kills_command_and_restarts_session(
    monkeypatch: pytest.MonkeyPatch, allow_extra: Callable[..., None]
) -> None:
    allow_extra("sleep")
    monkeypatch.setattr(bash_module, "_COMMAND_TIMEOUT_SECONDS", 1)

    with pytest.raises(BashToolError, match="did not finish within 1 seconds"):
        execute_bash({"command": "sleep 5"})

    assert execute_bash({"command": "echo recovered"}) == "recovered"


def test_invalid_utf8_output_does_not_break_the_session(
    allow_extra: Callable[..., None],
) -> None:
    allow_extra("printf")

    output = execute_bash({"command": "printf '\\xff\\xfe end'"})

    assert output.endswith("end")
    assert execute_bash({"command": "echo still-alive"}) == "still-alive"


def test_session_exit_is_reported_and_recovered(
    allow_extra: Callable[..., None],
) -> None:
    allow_extra("exit")

    with pytest.raises(BashToolError, match="session exited"):
        execute_bash({"command": "exit"})

    assert execute_bash({"command": "echo recovered"}) == "recovered"

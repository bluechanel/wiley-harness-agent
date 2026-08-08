"""Bash tool: run commands in one persistent bash session.

Follows the client-tool contract of Anthropic's bash tool docs: working
directory, environment variables and files persist between commands;
``restart: true`` discards the session; a command that outlives the timeout
gets its whole process group killed and the session replaced. The session
lives on the tool instance — ``DEFAULT_TOOLS`` holds one instance per process.

Every command is validated against an explicit allowlist before it runs;
anything else is rejected without executing. There is still no sandbox —
allowed commands run with the current user's privileges.
"""

import concurrent.futures
import logging
import os
import shlex
import signal
import subprocess
import uuid
from pathlib import Path

from wy_core import ApprovalRequest, Tool

_log = logging.getLogger(__name__)


class BashToolError(ValueError):
    """Raised when a bash request is invalid or the session breaks."""


_COMMAND_TIMEOUT_SECONDS = 120
_MAX_OUTPUT_LINES = 200
_MAX_OUTPUT_CHARS = 30_000
_MAX_LINE_CHARS = 100_000  # longest single read: bounds memory for newline-free floods
_MAX_CAPTURE_CHARS = 1_000_000  # stop storing past this; the reader keeps draining

ALLOWED_COMMANDS = {"ls", "cat", "echo", "pwd", "grep", "find", "wc", "head", "tail"}
SHELL_OPERATORS = {"&&", "||", "|", ";", "&", ">", "<", ">>"}


def validate_command(command: str) -> tuple[bool, str | None]:
    """Gate a command before execution: allowlist, not blocklist."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False, "Could not parse command"

    if not tokens:
        return False, "Empty command"

    executable = tokens[0]
    if executable not in ALLOWED_COMMANDS:
        return False, f"Command '{executable}' is not in the allowlist"

    # Reject shell operators written as standalone words
    for token in tokens[1:]:
        if token in SHELL_OPERATORS or token.startswith(("$", "`")):
            return False, f"Shell operator '{token}' is not allowed"

    return True, None


class BashSession:
    """A bash process that stays alive between commands so state persists."""

    def __init__(self) -> None:
        self._process = subprocess.Popen(
            ["/bin/bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # interleave errors with output, in order
            start_new_session=True,  # own process group: a timeout can kill every child
            text=True,
            errors="replace",  # bad bytes must not kill the reader mid-stream
        )

    def execute(self, command: str) -> tuple[str, int | None]:
        """Run one command; return (output, exit code). Exit code None means bash died."""
        sentinel = f"__WILEY_BASH_DONE_{uuid.uuid4().hex}__"  # unique per call
        try:
            self._process.stdin.write(f"{command}\necho {sentinel} $?\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            return "", None
        output: list[str] = []
        captured = 0
        while True:
            chunk = self._process.stdout.readline(_MAX_LINE_CHARS)
            if not chunk:
                return "".join(output), None  # EOF before the sentinel: bash exited
            if sentinel in chunk:  # this command's output is complete
                partial, _, status = chunk.partition(sentinel)
                if partial:
                    output.append(partial)
                try:
                    return "".join(output), int(status.strip())
                except ValueError:
                    return "".join(output), 0
            if captured < _MAX_CAPTURE_CHARS:
                output.append(chunk)
                captured += len(chunk)

    def kill(self) -> None:
        """Stop the shell and every process it started."""
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass  # group already gone: macOS reports EPERM for a zombie-only group, Linux ESRCH
        self._process.wait()


def _truncate_output(output: str) -> str:
    """Cap huge command output before it goes back into model context."""
    lines = output.splitlines()
    if len(lines) > _MAX_OUTPUT_LINES:
        output = "\n".join(lines[:_MAX_OUTPUT_LINES])
        output += f"\n\n... Output truncated ({len(lines)} total lines) ..."
    if len(output) > _MAX_OUTPUT_CHARS:
        output = (
            output[:_MAX_OUTPUT_CHARS]
            + f"\n\n... Output truncated ({_MAX_OUTPUT_CHARS} character limit) ..."
        )
    return output


class BashTool(Tool):
    name = "bash"
    description = (
        "Run commands in a persistent bash session. Working directory, "
        "environment variables and files persist between calls. Only "
        "allowlisted executables are permitted (ls, cat, echo, pwd, grep, "
        "find, wc, head, tail); standalone shell operators (&&, ||, |, ;, "
        "&, >, <, >>) and $/backtick expansions are rejected. Interactive "
        "commands that wait on stdin are not supported and will time out. "
        "Set restart=true to discard the session and start a fresh one."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to run. Required unless restart is true.",
            },
            "restart": {
                "type": "boolean",
                "description": "Kill the current bash session and start a new one.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._session: BashSession | None = None

    def approve(self, input: dict, workspace: Path) -> ApprovalRequest | None:
        """任何命令都需要用户审批。"""
        cmd = str(input.get("command", "")).strip()
        if not cmd:
            return None  # 空命令不执行，也无需审批
        fields: list[tuple[str, str]] = [("命令", cmd)]
        desc = str(input.get("description", "")).strip()
        if desc:
            fields.append(("说明", desc))
        return ApprovalRequest(
            heading="Bash 命令",
            question="是否执行该命令？",
            fields=fields,
            key=f"bash:{cmd}",
        )

    def _reset_session(self) -> None:
        if self._session is not None:
            self._session.kill()
            self._session = None

    def execute(self, input: dict) -> str:
        if input.get("restart"):
            self._reset_session()
            _log.info("bash session restarted")
            return "Bash session restarted"

        command = input.get("command")
        if not isinstance(command, str):
            raise BashToolError("Missing required argument: command")
        allowed, reason = validate_command(command)
        if not allowed:
            _log.warning("bash rejected command=%r reason=%s", command, reason)
            raise BashToolError(reason or "Command rejected")

        if self._session is None:
            self._session = BashSession()
        session = self._session
        _log.info("bash command=%r", command)  # audit before running: survives a hang
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(session.execute, command)
            try:
                output, exit_code = future.result(timeout=_COMMAND_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                self._reset_session()  # unblocks the reader thread as a side effect
                _log.warning("bash timeout command=%r", command)
                raise BashToolError(
                    f"command did not finish within {_COMMAND_TIMEOUT_SECONDS} seconds; "
                    "the bash session was restarted"
                ) from None

        _log.info("bash exit_code=%s output=%r", exit_code, output[:200])
        if exit_code is None:
            self._reset_session()
            message = "bash session exited; a fresh session starts on the next command"
            if output:
                message = f"{message}\nOutput before exit:\n{_truncate_output(output)}"
            raise BashToolError(message)

        output = _truncate_output(output).rstrip("\n")
        if exit_code != 0:
            status_note = f"Exit code: {exit_code}"
            output = f"{output}\n{status_note}" if output else status_note
        return output if output else "(no output)"


BASH = BashTool()

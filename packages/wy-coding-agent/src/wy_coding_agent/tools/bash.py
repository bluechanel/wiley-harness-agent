"""Bash tool: run commands in one persistent bash session.

Follows the client-tool contract of Anthropic's bash tool docs: working
directory, environment variables and files persist between commands;
``restart: true`` discards the session; a command that outlives the timeout
gets its whole process group killed and the session replaced. The session
lives on the tool instance — ``DEFAULT_TOOLS`` holds one instance per process,
so sub-agents share it; a lock keeps concurrent calls from interleaving into
the same shell.

Every command is graded by ``bash_policy.classify`` into allow / ask / deny:
``allow`` runs unattended, ``ask`` goes through the user approval layer
(``approve`` returns an ``ApprovalRequest``), ``deny`` is refused outright
(``approve`` raises, which the hook turns into a rejection). **The grading is
triage, not a boundary** — statically analysing bash cannot be complete. The
real boundary is user approval; there is no sandbox, and approved commands run
with the current user's privileges.
"""

import concurrent.futures
import logging
import os
import shlex
import signal
import subprocess
import threading
import uuid
from pathlib import Path

from wy_core import ApprovalRequest, Tool

from wy_coding_agent.tools.bash_policy import BashPolicy, classify

_log = logging.getLogger(__name__)


class BashToolError(ValueError):
    """Raised when a bash request is invalid, refused, or the session breaks."""


_COMMAND_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 600
_SYNTAX_CHECK_TIMEOUT_SECONDS = 10
_MAX_OUTPUT_LINES = 200
_HEAD_LINES = 120
_TAIL_LINES = 80
_MAX_OUTPUT_CHARS = 30_000
_HEAD_CHARS = 20_000
_TAIL_CHARS = 10_000
_MAX_LINE_CHARS = 100_000  # longest single read: bounds memory for newline-free floods
_MAX_CAPTURE_CHARS = 1_000_000  # stop storing past this; the reader keeps draining

# Pagers and credential prompts are the top hang sources for a pipe-fed shell:
# they wait on a terminal that will never answer, so the command only ends at
# the timeout — which also costs the session.
_SESSION_PRELUDE = "export GIT_PAGER=cat PAGER=cat TERM=dumb GIT_TERMINAL_PROMPT=0\n"


def _truncate_output(output: str) -> str:
    """Cap huge command output before it goes back into model context.

    Truncates the middle, not the tail: a test run's verdict and a compiler's
    error summary both live at the end of the output.
    """
    lines = output.splitlines()
    if len(lines) > _MAX_OUTPUT_LINES:
        omitted = len(lines) - _HEAD_LINES - _TAIL_LINES
        output = "\n".join(
            lines[:_HEAD_LINES]
            + ["", f"... {omitted} lines omitted ({len(lines)} total lines) ...", ""]
            + lines[-_TAIL_LINES:]
        )
    if len(output) > _MAX_OUTPUT_CHARS:
        omitted = len(output) - _HEAD_CHARS - _TAIL_CHARS
        output = (
            output[:_HEAD_CHARS]
            + f"\n\n... {omitted} characters omitted ...\n\n"
            + output[-_TAIL_CHARS:]
        )
    return output


def _check_syntax(command: str) -> None:
    """Reject a syntactically incomplete command before it reaches the session.

    An unterminated quote or heredoc makes bash keep reading, so it swallows
    the sentinel line and the call hangs until the timeout kills the whole
    session. ``bash -n`` parses without executing, so the model gets a real
    error message instead.
    """
    try:
        check = subprocess.run(
            ["/bin/bash", "-n"],
            input=command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_SYNTAX_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return  # the check is a courtesy; never block a command because it failed
    if check.returncode != 0:
        detail = check.stderr.strip() or "syntax error"
        raise BashToolError(f"bash syntax error: {detail}")


class BashSession:
    """A bash process that stays alive between commands so state persists."""

    def __init__(self, cwd: str | None = None) -> None:
        self._process = subprocess.Popen(
            ["/bin/bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # interleave errors with output, in order
            start_new_session=True,  # own process group: a timeout can kill every child
            text=True,
            errors="replace",  # bad bytes must not kill the reader mid-stream
        )
        self.cwd = cwd or os.getcwd()
        self._output: list[str] = []  # current command's output; readable after a timeout
        prelude = _SESSION_PRELUDE
        if cwd:  # restored after a forced restart; a stale path must not break startup
            prelude += f"cd {shlex.quote(cwd)} >/dev/null 2>&1 || true\n"
        try:
            self._process.stdin.write(prelude)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # a dead shell is reported on the first execute

    def partial_output(self) -> str:
        """Output collected so far — what a timed-out command managed to print."""
        return "".join(list(self._output))

    def execute(self, command: str) -> tuple[str, int | None]:
        """Run one command; return (output, exit code). Exit code None means bash died."""
        sentinel = f"__WILEY_BASH_DONE_{uuid.uuid4().hex}__"  # unique per call
        # The command runs in a group with stdin closed: anything that waits on
        # input (`cat`, a bare `python`, `git commit` without -m) would otherwise
        # eat the sentinel line and hang until the timeout.
        payload = (
            f"{{\n{command}\n}} </dev/null\n"
            f"printf '%s %s %s\\n' {sentinel} \"$?\" \"$PWD\"\n"
        )
        self._output = []
        try:
            self._process.stdin.write(payload)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            return "", None
        captured = 0
        while True:
            chunk = self._process.stdout.readline(_MAX_LINE_CHARS)
            if not chunk:
                return self.partial_output(), None  # EOF before the sentinel: bash exited
            if sentinel in chunk:  # this command's output is complete
                partial, _, status = chunk.partition(sentinel)
                if partial:
                    self._output.append(partial)
                return self.partial_output(), self._read_status(status)
            if captured < _MAX_CAPTURE_CHARS:
                self._output.append(chunk)
                captured += len(chunk)

    def _read_status(self, status: str) -> int:
        """Parse the sentinel trailer ``<exit code> <cwd>``; remember the cwd."""
        fields = status.strip().split(None, 1)
        if len(fields) == 2 and fields[1].startswith("/"):
            self.cwd = fields[1]
        try:
            return int(fields[0])
        except (IndexError, ValueError):
            return 0

    def kill(self) -> None:
        """Stop the shell and every process it started."""
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass  # group already gone: macOS reports EPERM for a zombie-only group, Linux ESRCH
        self._process.wait()


class BashTool(Tool):
    name = "bash"
    description = (
        "Run commands in a persistent bash session. Working directory, "
        "environment variables and files persist between calls, so `cd` and "
        "exports carry over. Commands are graded before running: read-only "
        "ones run directly, anything else asks the user for approval, and a "
        "small set of destructive commands is refused outright. "
        "Prefer the read, grep and glob tools over cat, grep and find — they "
        "are faster and their output is easier to work with. "
        "Interactive commands that wait on stdin are not supported: stdin is "
        "closed, so pass arguments instead (git commit -m, python -c). "
        "Quote paths that contain spaces. Set timeout (seconds) for commands "
        "that need longer than the default. Set restart=true to discard the "
        "session and start a fresh one."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to run. Required unless restart is true.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in "
                    "5-10 words. Shown to the user when approval is needed."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Optional timeout in seconds (default "
                    f"{_COMMAND_TIMEOUT_SECONDS}, max {_MAX_TIMEOUT_SECONDS})."
                ),
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
        self._cwd: str | None = None  # survives a forced restart
        self._policy = BashPolicy()
        self._timeout: int | None = None
        self._lock = threading.Lock()

    def configure(
        self, policy: BashPolicy | None = None, *, timeout: int | None = None
    ) -> None:
        """Install the grading policy and default timeout (called by ``bootstrap``)."""
        self._policy = policy if policy is not None else BashPolicy()
        self._timeout = timeout

    def approve(self, input: dict, workspace: Path) -> ApprovalRequest | None:
        """Grade the command: allow runs silently, ask prompts, deny raises.

        Raising is how a hard refusal reaches the user: ``WorkspaceToolHook``
        turns an exception from ``approve()`` into a rejected call, so a denied
        command never gets an approval prompt.
        """
        command = str(input.get("command", "")).strip()
        if not command:
            return None  # 空命令不执行，也无需审批
        verdict = classify(command, self._policy)
        if verdict.decision == "deny":
            raise BashToolError(f"命令被策略拒绝：{verdict.reason}")
        if verdict.decision == "allow":
            return None
        fields: list[tuple[str, str]] = [("命令", command)]
        description = str(input.get("description", "")).strip()
        if description:
            fields.append(("说明", description))
        fields.append(("原因", verdict.reason))
        return ApprovalRequest(
            heading="Bash 命令",
            question="是否执行该命令？",
            fields=fields,
            key=f"bash:{command}",
        )

    def _reset_session(self, *, keep_cwd: bool = False) -> None:
        if self._session is not None:
            self._cwd = self._session.cwd if keep_cwd else None
            self._session.kill()
            self._session = None
        elif not keep_cwd:
            self._cwd = None

    def _resolve_timeout(self, raw: object) -> int:
        if raw is None:
            return self._timeout if self._timeout is not None else _COMMAND_TIMEOUT_SECONDS
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise BashToolError("timeout must be a positive integer number of seconds")
        return min(raw, _MAX_TIMEOUT_SECONDS)

    def execute(self, input: dict) -> str:
        if input.get("restart"):
            with self._lock:
                self._reset_session()
            _log.info("bash session restarted")
            return "Bash session restarted"

        command = input.get("command")
        if not isinstance(command, str):
            raise BashToolError("Missing required argument: command")
        if not command.strip():
            raise BashToolError("Empty command")
        # Second gate: a denied command must not run even without an approval
        # hook (programmatic callers, headless hosts).
        verdict = classify(command, self._policy)
        if verdict.decision == "deny":
            _log.warning("bash denied command=%r reason=%s", command, verdict.reason)
            raise BashToolError(f"命令被策略拒绝：{verdict.reason}")
        timeout = self._resolve_timeout(input.get("timeout"))
        _check_syntax(command)

        with self._lock:  # one shell, one command at a time
            return self._run(command, timeout)

    def _run(self, command: str, timeout: int) -> str:
        if self._session is None:
            self._session = BashSession(cwd=self._cwd)
        session = self._session
        _log.info("bash command=%r", command)  # audit before running: survives a hang
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(session.execute, command)
            try:
                output, exit_code = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                partial = session.partial_output()
                self._reset_session(keep_cwd=True)  # unblocks the reader thread too
                _log.warning("bash timeout command=%r", command)
                message = (
                    f"command did not finish within {timeout} seconds; "
                    "the bash session was restarted"
                )
                if partial:
                    message = f"{message}\nOutput before the timeout:\n{_truncate_output(partial)}"
                raise BashToolError(message) from None

        _log.info("bash exit_code=%s output=%r", exit_code, output[:200])
        if exit_code is None:
            self._reset_session(keep_cwd=True)
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

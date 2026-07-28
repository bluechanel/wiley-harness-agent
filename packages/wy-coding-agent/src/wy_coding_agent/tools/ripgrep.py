"""ripgrep 共享辅助:定位并调用 ``rg``(grep/glob 工具共用)。

``rg`` 由 PyPI ``ripgrep`` 依赖提供(装进环境的 scripts 目录),PATH 兜底。
"""

import os
import shutil
import subprocess
import sysconfig
from collections.abc import Callable
from pathlib import Path

# Version control directories excluded from every search; they only add noise.
VCS_DIRECTORIES = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

SEARCH_TIMEOUT_SECONDS = 30


def rg_binary() -> str | None:
    """Locate rg: the ripgrep wheel installs it in the scripts directory."""
    executable = "rg.exe" if os.name == "nt" else "rg"
    candidate = Path(sysconfig.get_path("scripts")) / executable
    if candidate.is_file():
        return str(candidate)
    return shutil.which(executable)


def run_ripgrep(args: list[str], *, error: Callable[[str], Exception]) -> list[str]:
    """Run rg and return stdout lines; exit 1 (no matches) is not an error.

    Failures raise via ``error`` so each tool reports its own exception type.
    """
    executable = rg_binary()
    if executable is None:
        raise error(
            "ripgrep binary not found; install the 'ripgrep' package or put rg on PATH"
        )
    try:
        process = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise error(
            f"search did not complete within {SEARCH_TIMEOUT_SECONDS} seconds; "
            "narrow the path, glob, or pattern"
        ) from None
    # Exit 2 with output means some files errored but matches were still found.
    if process.returncode not in (0, 1) and not process.stdout:
        detail = process.stderr.strip() or f"exit code {process.returncode}"
        raise error(f"ripgrep failed: {detail[:500]}")
    return process.stdout.splitlines()

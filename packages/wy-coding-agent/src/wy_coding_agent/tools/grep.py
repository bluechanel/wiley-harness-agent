"""Grep tool: search file contents with a regular expression, backed by ripgrep.

Modeled on the ripgrep-backed Grep tool of coding agents. The ``rg`` binary is
provided by the ``ripgrep`` PyPI dependency (installed into the environment's
scripts directory), with a PATH lookup as fallback, so the tool works wherever
the library is installed.

The harness shells out to ``rg`` per call and post-processes its output:
pagination via ``head_limit``/``offset`` with a default cap of 250 and ``0``
as the unlimited escape hatch, mtime-sorted file lists, path relativization,
and a trailing pagination note only when truncation actually occurred. Search
semantics (``.gitignore`` handling, glob/type filters, context lines, binary
detection) are ripgrep's own.
"""

import os
import shutil
import subprocess
import sysconfig
from pathlib import Path
from typing import Any, Mapping

from wy_core import Tool


class GrepToolError(ValueError):
    """Raised when a grep request is invalid or the search cannot complete."""


# Version control directories excluded from every search; they only add noise.
_VCS_DIRECTORIES = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

# Default cap when head_limit is unspecified; generous for exploration while
# preventing context bloat. head_limit=0 is the explicit unlimited escape hatch.
_DEFAULT_HEAD_LIMIT = 250
_MAX_COLUMNS = 500  # keep minified/base64 lines from flooding output
_MAX_OUTPUT_CHARS = 30_000  # final cap, same budget as the bash tool
_SEARCH_TIMEOUT_SECONDS = 30


def _rg_binary() -> str:
    """Locate rg: the ripgrep wheel installs it in the scripts directory."""
    executable = "rg.exe" if os.name == "nt" else "rg"
    candidate = Path(sysconfig.get_path("scripts")) / executable
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(executable)
    if found:
        return found
    raise GrepToolError(
        "ripgrep binary not found; install the 'ripgrep' package or put rg on PATH"
    )


def _int_arg(arguments: Mapping[str, Any], name: str, default: int | None) -> int | None:
    value = arguments.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GrepToolError(f"{name} must be a non-negative integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise GrepToolError(f"{name} must be a non-negative integer")
        value = int(value)
    if value < 0:
        raise GrepToolError(f"{name} must be a non-negative integer")
    return int(value)


def _bool_arg(arguments: Mapping[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GrepToolError(f"{name} must be a boolean")
    return value


def _split_globs(glob: str) -> list[str]:
    """Split on whitespace and commas, but keep brace patterns whole."""
    patterns: list[str] = []
    for raw in glob.split():
        if "{" in raw and "}" in raw:
            patterns.append(raw)
        else:
            patterns.extend(part for part in raw.split(",") if part)
    return patterns


def _apply_head_limit(
    items: list[Any], limit: int | None, offset: int
) -> tuple[list[Any], int | None]:
    """Slice like `| tail -n +offset | head -N`; report the limit only on truncation."""
    if limit == 0:
        return items[offset:], None
    effective = limit if limit is not None else _DEFAULT_HEAD_LIMIT
    sliced = items[offset : offset + effective]
    truncated = len(items) - offset > effective
    return sliced, (effective if truncated else None)


def _format_limit_info(applied_limit: int | None, offset: int) -> str:
    parts: list[str] = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if offset:
        parts.append(f"offset: {offset}")
    return ", ".join(parts)


def _truncate_chars(output: str) -> str:
    if len(output) > _MAX_OUTPUT_CHARS:
        output = (
            output[:_MAX_OUTPUT_CHARS]
            + f"\n\n... Output truncated ({_MAX_OUTPUT_CHARS} character limit) ..."
        )
    return output


def _run_ripgrep(args: list[str]) -> list[str]:
    """Run rg and return stdout lines; exit 1 (no matches) is not an error."""
    try:
        process = subprocess.run(
            [_rg_binary(), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_SEARCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise GrepToolError(
            f"search did not complete within {_SEARCH_TIMEOUT_SECONDS} seconds; "
            "narrow the path, glob, or pattern"
        ) from None
    # Exit 2 with output means some files errored but matches were still found.
    if process.returncode not in (0, 1) and not process.stdout:
        detail = process.stderr.strip() or f"exit code {process.returncode}"
        raise GrepToolError(f"ripgrep failed: {detail[:500]}")
    return process.stdout.splitlines()


class GrepTool(Tool):
    name = "grep"
    description = (
        "A powerful search tool built on ripgrep. Searches file contents "
        "with a regular expression (Rust regex syntax). Respects .gitignore "
        "in git repositories, skips binary files and VCS directories, and "
        "searches hidden files. Output modes: files_with_matches (default) "
        "lists matching file paths sorted by modification time; content "
        "shows matching lines (supports -A/-B/-C context and -n line "
        "numbers); count shows per-file match counts. Filter files with "
        "glob or type. Results are paginated: head_limit defaults to 250, "
        "0 means unlimited, offset skips leading entries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "The regular expression pattern to search for in file "
                    "contents (Rust regex syntax, as used by ripgrep)."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "File or directory to search in. Defaults to the current "
                    "working directory."
                ),
            },
            "glob": {
                "type": "string",
                "description": (
                    'Glob pattern(s) to filter files, e.g. "*.py" or '
                    '"*.{ts,tsx}"; separate multiple patterns with spaces or '
                    "commas, prefix with ! to exclude. Patterns without a "
                    "slash match the file name at any depth."
                ),
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    'Output mode: "content" shows matching lines (supports '
                    '-A/-B/-C context, -n line numbers, head_limit), '
                    '"files_with_matches" shows file paths (supports '
                    'head_limit), "count" shows match counts (supports '
                    'head_limit). Defaults to "files_with_matches".'
                ),
            },
            "-B": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Number of lines to show before each match. Requires "
                    'output_mode: "content", ignored otherwise.'
                ),
            },
            "-A": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Number of lines to show after each match. Requires "
                    'output_mode: "content", ignored otherwise.'
                ),
            },
            "-C": {
                "type": "integer",
                "minimum": 0,
                "description": "Alias for context.",
            },
            "context": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Number of lines to show before and after each match. "
                    'Requires output_mode: "content", ignored otherwise.'
                ),
            },
            "-n": {
                "type": "boolean",
                "description": (
                    "Show line numbers in output. Requires output_mode: "
                    '"content", ignored otherwise. Defaults to true.'
                ),
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search.",
            },
            "type": {
                "type": "string",
                "description": (
                    "File type to search (rg --type). Common types: py, js, "
                    "ts, rust, go, java, etc. More efficient than glob for "
                    "standard file types."
                ),
            },
            "head_limit": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Limit output to first N lines/entries, equivalent to "
                    '"| head -N". Works across all output modes. Defaults to '
                    "250 when unspecified. Pass 0 for unlimited (use "
                    "sparingly - large result sets waste context)."
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Skip first N lines/entries before applying head_limit. "
                    "Works across all output modes. Defaults to 0."
                ),
            },
            "multiline": {
                "type": "boolean",
                "description": (
                    "Enable multiline mode where . matches newlines and "
                    "patterns can span lines (rg -U --multiline-dotall). "
                    "Default: false."
                ),
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def execute(self, input: dict) -> str:
        pattern = input.get("pattern")
        if not isinstance(pattern, str):
            raise GrepToolError("Missing required argument: pattern")

        output_mode = input.get("output_mode", "files_with_matches")
        if output_mode not in ("content", "files_with_matches", "count"):
            raise GrepToolError(f"Unsupported output_mode: {output_mode!r}")

        show_numbers = _bool_arg(input, "-n", True)
        head_limit = _int_arg(input, "head_limit", None)
        offset = _int_arg(input, "offset", 0) or 0

        path_arg = input.get("path")
        if path_arg is not None and not isinstance(path_arg, str):
            raise GrepToolError("path must be a string")
        root = Path(os.path.abspath(Path(path_arg).expanduser())) if path_arg else Path.cwd()
        if not root.exists():
            raise GrepToolError(f"Path does not exist: {path_arg}")

        args = ["--hidden", "--with-filename"]
        for directory in _VCS_DIRECTORIES:
            args.extend(["--glob", f"!{directory}"])
        args.extend(["--max-columns", str(_MAX_COLUMNS)])

        if _bool_arg(input, "multiline", False):
            args.extend(["-U", "--multiline-dotall"])
        if _bool_arg(input, "-i", False):
            args.append("-i")

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        elif show_numbers:
            args.append("-n")

        if output_mode == "content":
            # context (full name) takes precedence over -C, which overrides -B/-A.
            context = _int_arg(input, "context", None)
            if context is None:
                context = _int_arg(input, "-C", None)
            if context is not None:
                args.extend(["-C", str(context)])
            else:
                before = _int_arg(input, "-B", None)
                after = _int_arg(input, "-A", None)
                if before is not None:
                    args.extend(["-B", str(before)])
                if after is not None:
                    args.extend(["-A", str(after)])

        type_arg = input.get("type")
        if type_arg is not None:
            if not isinstance(type_arg, str):
                raise GrepToolError("type must be a string")
            args.extend(["--type", type_arg])

        glob_arg = input.get("glob")
        if glob_arg is not None:
            if not isinstance(glob_arg, str):
                raise GrepToolError("glob must be a string")
            for glob_pattern in _split_globs(glob_arg):
                args.extend(["--glob", glob_pattern])

        # A leading dash would be parsed as an option, so pass the pattern via -e.
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)
        args.append(str(root))

        results = _run_ripgrep(args)

        # rg prints paths joined onto the absolute root; relativize against CWD.
        cwd = Path.cwd()
        prefix = str(cwd) + os.sep

        def relativize(line: str) -> str:
            return line[len(prefix) :] if line.startswith(prefix) else line

        if output_mode == "content":
            shown, applied_limit = _apply_head_limit(results, head_limit, offset)
            limit_info = _format_limit_info(applied_limit, offset)
            body = "\n".join(relativize(line) for line in shown)
            result = _truncate_chars(body) if body else "No matches found"
            if limit_info:
                result += f"\n\n[Showing results with pagination = {limit_info}]"
            return result

        if output_mode == "count":
            shown, applied_limit = _apply_head_limit(results, head_limit, offset)
            limit_info = _format_limit_info(applied_limit, offset)
            final_lines = [relativize(line) for line in shown]
            total = 0
            file_count = 0
            for line in final_lines:  # lines have the format path:count
                _, _, count_text = line.rpartition(":")
                try:
                    total += int(count_text)
                except ValueError:
                    continue
                file_count += 1
            raw = _truncate_chars("\n".join(final_lines)) if final_lines else "No matches found"
            summary = (
                f"\n\nFound {total} total "
                f"{'occurrence' if total == 1 else 'occurrences'} across "
                f"{file_count} {'file' if file_count == 1 else 'files'}."
            )
            if limit_info:
                summary += f" with pagination = {limit_info}"
            return raw + summary

        # files_with_matches (default): most recently modified first, name as tiebreak
        def mtime(path_text: str) -> float:
            try:
                return os.stat(path_text).st_mtime
            except OSError:
                return 0.0

        results.sort(key=lambda path_text: (-mtime(path_text), path_text))
        shown, applied_limit = _apply_head_limit(results, head_limit, offset)
        if not shown:
            return "No files found"
        limit_info = _format_limit_info(applied_limit, offset)
        header = f"Found {len(shown)} {'file' if len(shown) == 1 else 'files'}"
        if limit_info:
            header += f" {limit_info}"
        return _truncate_chars(header + "\n" + "\n".join(relativize(line) for line in shown))


GREP = GrepTool()

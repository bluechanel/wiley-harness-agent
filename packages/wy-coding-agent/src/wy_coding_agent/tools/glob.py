"""Glob tool: find files by name pattern, modeled on Claude Code's GlobTool.

``rg --files`` enumerates the candidates so ignore semantics match the grep
tool: .gitignore is respected inside git repositories, hidden files are
included, VCS directories are excluded. Pattern matching happens harness-side
because rg's ``--glob`` is a whitelist that overrides ignore rules and would
resurface gitignored files. The harness sorts matches by modification time
(most recent first, same as grep's file lists), relativizes paths against the
CWD and caps results at 100 files with an explicit truncation note.
"""

import os
import re
from pathlib import Path

from wy_core import Tool

from wy_coding_agent.tools.ripgrep import VCS_DIRECTORIES, run_ripgrep


class GlobToolError(ValueError):
    """Raised when a glob request is invalid or the search cannot complete."""


_MAX_RESULTS = 100
_TRUNCATION_NOTE = (
    "(Results are truncated. Consider using a more specific path or pattern.)"
)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a regex.

    ``*`` and ``?`` never cross ``/``; ``**`` at a segment boundary matches
    zero or more whole segments; an unterminated ``[`` class is literal.
    """
    index, length = 0, len(pattern)
    parts: list[str] = []
    while index < length:
        char = pattern[index]
        if char == "*":
            star_end = index
            while star_end < length and pattern[star_end] == "*":
                star_end += 1
            is_multi = star_end - index > 1
            at_boundary = index == 0 or pattern[index - 1] == "/"
            if is_multi and at_boundary and star_end < length and pattern[star_end] == "/":
                parts.append("(?:[^/]+/)*")  # "**/" spans zero or more segments
                index = star_end + 1
            elif is_multi:
                parts.append(".*")  # bare or trailing "**"
                index = star_end
            else:
                parts.append("[^/]*")
                index = star_end
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[":
            class_end = index + 1
            if class_end < length and pattern[class_end] in "!^":
                class_end += 1
            if class_end < length and pattern[class_end] == "]":
                class_end += 1  # a leading "]" is a literal member
            while class_end < length and pattern[class_end] != "]":
                class_end += 1
            if class_end >= length:
                parts.append(re.escape("["))
                index += 1
            else:
                inner = pattern[index + 1 : class_end]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                index = class_end + 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("".join(parts) + r"\Z")


class GlobTool(Tool):
    name = "glob"
    description = (
        "Fast file pattern matching tool that works with any codebase size.\n"
        '- Supports glob patterns like "**/*.js" or "src/**/*.ts"; patterns '
        "without a slash match the file name at any depth\n"
        "- Returns matching file paths sorted by modification time, most "
        "recent first\n"
        "- Respects .gitignore in git repositories, skips VCS directories, "
        "includes hidden files\n"
        f"- Results are capped at {_MAX_RESULTS} files; narrow the pattern or "
        "path when truncated\n"
        "- Use this tool to find files by name pattern; to search file "
        "contents, use the grep tool"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against.",
            },
            "path": {
                "type": "string",
                "description": (
                    "The directory to search in. If not specified, the "
                    "current working directory will be used. IMPORTANT: Omit "
                    "this field to use the default directory. DO NOT enter "
                    '"undefined" or "null" - simply omit it for the default '
                    "behavior. Must be a valid directory path if provided."
                ),
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def execute(self, input: dict) -> str:
        pattern = input.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise GlobToolError("Missing required argument: pattern")

        path_arg = input.get("path")
        if path_arg is not None and not isinstance(path_arg, str):
            raise GlobToolError("path must be a string")
        root = Path(os.path.abspath(Path(path_arg).expanduser())) if path_arg else Path.cwd()
        if not root.exists():
            raise GlobToolError(
                f"Directory does not exist: {path_arg}. Current working "
                f"directory is {Path.cwd()}."
            )
        if not root.is_dir():
            raise GlobToolError(f"Path is not a directory: {path_arg}")

        args = ["--files", "--hidden"]
        for directory in VCS_DIRECTORIES:
            args.extend(["--glob", f"!{directory}"])
        args.append(str(root))

        candidates = run_ripgrep(args, error=GlobToolError)

        # Patterns with a slash match the path relative to the search root;
        # slashless patterns match the file name at any depth.
        matcher = _glob_to_regex(pattern)
        by_basename = "/" not in pattern
        root_prefix = str(root).rstrip(os.sep) + os.sep

        def matches(line: str) -> bool:
            if by_basename:
                target = os.path.basename(line)
            elif line.startswith(root_prefix):
                target = line[len(root_prefix) :]
            else:
                target = line
            return matcher.match(target) is not None

        results = [line for line in candidates if matches(line)]

        # Most recently modified first, name as tiebreak — same order as the
        # grep tool's files_with_matches mode.
        def mtime(path_text: str) -> float:
            try:
                return os.stat(path_text).st_mtime
            except OSError:
                return 0.0

        results.sort(key=lambda path_text: (-mtime(path_text), path_text))
        truncated = len(results) > _MAX_RESULTS
        shown = results[:_MAX_RESULTS]
        if not shown:
            return "No files found"

        # rg prints paths joined onto the absolute root; relativize against CWD.
        cwd_prefix = str(Path.cwd()) + os.sep
        lines = [
            line[len(cwd_prefix) :] if line.startswith(cwd_prefix) else line
            for line in shown
        ]
        if truncated:
            lines.append(_TRUNCATION_NOTE)
        return "\n".join(lines)


GLOB = GlobTool()

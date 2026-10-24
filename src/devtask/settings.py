"""Settings that `devtask config` reads and writes.

Each setting lives in the global config.toml and, unless it is global-only,
in a repo's project config.toml, whose value takes precedence. List settings
add up instead: the defaults, then the global list, then the project list.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath

from devtask import core

STRATEGIES = ("on_branch", "on_edit")
PLUGIN_SCOPES = ("local", "project")

# Paths that are documentation only. Edits to them never start a task.
DEFAULT_IGNORE = [
    # Prose formats
    "*.md", "*.mdx", "*.markdown", "*.rst", "*.adoc", "*.asciidoc",
    "*.org", "*.rdoc", "*.pod",
    # Documentation directories
    "docs/**", "doc/**", "documentation/**", "man/**",
    # Standard project prose files, with any extension
    "README*", "CHANGELOG*", "CHANGES*", "HISTORY*", "NEWS*",
    "AUTHORS*", "CONTRIBUTORS*", "CONTRIBUTING*", "CODE_OF_CONDUCT*",
    "SECURITY*", "LICENSE*", "COPYING*", "NOTICE*",
]  # fmt: skip


class SettingError(ValueError):
    pass


def parse_randomize(raw: str) -> float:
    if raw == "off":
        return 0.0
    try:
        p = float(raw)
    except ValueError:
        raise SettingError(f"randomize must be a number from 0 to 1, or off (got {raw!r})")
    if not 0 <= p <= 1:
        raise SettingError(f"randomize must be from 0 to 1 (got {p})")
    return p


def parse_strategy(raw: str) -> str:
    if raw not in STRATEGIES:
        raise SettingError(f"randomization_strategy must be one of {', '.join(STRATEGIES)}")
    return raw


def parse_bool(raw: str) -> bool:
    match raw.lower():
        case "true" | "on" | "yes":
            return True
        case "false" | "off" | "no":
            return False
    raise SettingError(f"expected true or false (got {raw!r})")


def parse_plugin_scope(raw: str) -> str:
    if raw not in PLUGIN_SCOPES:
        raise SettingError(f"plugin_scope must be one of {', '.join(PLUGIN_SCOPES)}")
    return raw


def parse_tag(raw: str) -> str:
    if not raw or raw.isspace():
        raise SettingError("tag must not be empty")
    return raw


@dataclass(frozen=True)
class Setting:
    help: str
    default: object
    parse: Callable[[str], object] | None = None  # None for list settings
    choices: tuple[str, ...] = ()
    global_only: bool = False


SETTINGS: dict[str, Setting] = {
    "tag": Setting("tag prefix", core.DEFAULT_TAG, parse_tag, global_only=True),
    "randomize": Setting(
        "chance (0 to 1) that a new task starts in devtask mode", 0.0, parse_randomize, ("off",)
    ),
    "randomization_strategy": Setting(
        "when to roll: a new branch, or the first code edit",
        "on_branch",
        parse_strategy,
        STRATEGIES,
    ),
    "randomize_ignore": Setting("paths whose edits never start a task", DEFAULT_IGNORE),
    "git_exclude": Setting(
        "list the files devtask init writes in .git/info/exclude",
        True,
        parse_bool,
        ("true", "false"),
    ),
    "plugin_scope": Setting(
        "where devtask init enables the Claude Code plugin",
        "local",
        parse_plugin_scope,
        PLUGIN_SCOPES,
    ),
}


@dataclass(frozen=True)
class Value:
    value: object
    source: str  # "default", "global" or "project"


def lookup(key: str, repo: core.Repo | None = None) -> Value:
    """The effective value of a scalar setting and where it came from."""
    setting = SETTINGS[key]
    if repo is not None and not setting.global_only:
        project = core.read_toml(repo.config_file)
        if key in project:
            return Value(project[key], "project")
    glob = core.read_toml(core.global_config_file())
    if key in glob:
        return Value(glob[key], "global")
    return Value(setting.default, "default")


def ignore_patterns(repo: core.Repo | None = None) -> list[tuple[str, str]]:
    """Every randomize_ignore pattern in order, as (pattern, source)."""
    out = [(p, "default") for p in DEFAULT_IGNORE]
    out += [
        (p, "global") for p in core.read_toml(core.global_config_file()).get("randomize_ignore", [])
    ]
    if repo is not None:
        out += [
            (p, "project") for p in core.read_toml(repo.config_file).get("randomize_ignore", [])
        ]
    return out


def randomize(repo: core.Repo) -> float:
    """The chance a new task in `repo` rolls devtask mode; 0 until `devtask init`."""
    if not repo.initialized():
        return 0.0
    value = lookup("randomize", repo).value
    return float(value) if isinstance(value, int | float) else 0.0


def strategy(repo: core.Repo) -> str:
    value = lookup("randomization_strategy", repo).value
    return value if isinstance(value, str) and value in STRATEGIES else "on_branch"


def plugin_scope(repo: core.Repo) -> str:
    value = lookup("plugin_scope", repo).value
    return value if isinstance(value, str) and value in PLUGIN_SCOPES else "local"


# Patterns follow .gitignore: without a "/" they match the file name at any
# depth; with one they match from the repo root; "**" spans directories; the
# last matching pattern wins and a leading "!" makes a path count as code.


@cache
def glob_re(pattern: str) -> re.Pattern[str]:
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.strip("/")
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    # A pattern matching a directory also matches everything below it.
    return re.compile(("" if anchored else "(?:.*/)?") + out + "(?:/.*)?")


def ignored(rel: PurePosixPath | Path, patterns: list[str]) -> bool:
    """True when the repo-relative path matches randomize_ignore."""
    path = PurePosixPath(rel).as_posix()
    result = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        if glob_re(pattern.removeprefix("!")).fullmatch(path):
            result = not negate
    return result


# Writing: one key's lines are replaced in place, so the rest of the file,
# comments included, stays as the user wrote it.


def toml_value(value: object) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int() | float():
            return repr(value)
        case str():
            return json.dumps(value)
        case list():
            return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"can't write {value!r} as TOML")


def _key_lines(lines: list[str], key: str) -> tuple[int, int] | None:
    """The [start, end) lines of a top-level `key = ...`, if present."""
    key_re = re.compile(rf"^\s*(?:{re.escape(key)}|\"{re.escape(key)}\")\s*=")
    for start, line in enumerate(lines):
        if re.match(r"^\s*\[", line):
            return None  # past the top-level keys
        if key_re.match(line):
            depth, end = 0, start
            for end in range(start, len(lines)):
                # Ignore brackets inside strings and comments when balancing arrays.
                code = re.sub(r"\"(?:\\.|[^\"\\])*\"|'[^']*'|#.*", "", lines[end])
                depth += code.count("[") - code.count("]")
                if depth <= 0:
                    break
            return start, end + 1
    return None


def write_key(path: Path, key: str, value: object | None) -> None:
    """Set `key` in a TOML file, or remove it when `value` is None."""
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    span = _key_lines(lines, key)
    new = [] if value is None else [f"{key} = {toml_value(value)}\n"]
    if span is not None:
        lines[span[0] : span[1]] = new
    else:
        # Top-level keys must come before the first [table].
        at = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[", line)), len(lines))
        if at == len(lines) and lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines[at:at] = new
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines))

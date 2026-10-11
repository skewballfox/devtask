"""Claude Code hook: in a guarded repo, Claude may only add or remove tag lines.

`session-start` tells Claude the guard is on. `pre-tool-use` denies file edits
that change anything but tag-only lines, and Bash commands that look like they
write to the repo. The Bash check is a heuristic; the git hook is the backstop.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from devtask import core

FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

# Commands that write to the working tree or history. Moving between branches
# (checkout, switch, pull, gh pr checkout) stays allowed for reading PRs.
_BASH_DENY = [
    (r"\bsed\b[^|;&]*\s-[A-Za-z]*i", "sed -i edits files in place"),
    (r"\bperl\b[^|;&]*\s-[A-Za-z]*i", "perl -i edits files in place"),
    (r"\btee\b", "tee writes files"),
    (r"\b(rm|mv|cp|touch|truncate|patch|dd|ln|install)\b", "file-changing command"),
    (
        (
            r"\bgit\s+(add|commit|apply|am|stash|reset|restore|merge|rebase|cherry-pick"
            r"|revert|rm|mv|clean|push|checkout\s+(--|\.)\s*)"
        ),
        "git command that changes files or history",
    ),
    (
        (
            r"\b(cargo\s+(fmt|fix|clippy\s+[^|;&]*--fix)|rustfmt|gofmt\s+-w|black|isort"
            r"|ruff\s+(format|check\s+[^|;&]*--fix)|prettier\s+[^|;&]*--write"
            r"|eslint\s+[^|;&]*--fix|clang-format\s+[^|;&]*-i)\b"
        ),
        "formatter or auto-fixer",
    ),
]
_REDIRECT = re.compile(r"(?<![0-9&<>])>>?\s*(?!&)([^\s;|&)]+)")


def emit(event: str, **fields: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def deny(reason: str) -> None:
    emit(
        "PreToolUse",
        permissionDecision="deny",
        permissionDecisionReason=(
            f"devtask guard: {reason}. The user turned on the devtask guard for "
            "this repository because they want to write the code themselves. "
            "Changing it beyond whole-line tag comments goes against what they "
            "asked for, including through a different tool or command. Put the "
            "change in the task file as a description, or ask the user. Run "
            "`devtask guard off` only if the user tells you to."
        ),
    )
    sys.exit(0)


def guarded_repo(path: Path) -> core.Repo | None:
    root = core.repo_root(path)
    if root is None:
        return None
    repo = core.Repo.at(root)
    return repo if repo.guard_enabled() else None


def inside(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not rel.parts or rel.parts[0] != ".git"


def check_file_tool(tool: str, inp: dict, cwd: str) -> None:
    raw = inp.get("file_path") or inp.get("notebook_path")
    if not raw:
        return
    path = Path(cwd, raw)
    repo = guarded_repo(path)
    if repo is None or not inside(path, repo.root):
        return
    prefix = core.tag_prefix()
    match tool:
        case "NotebookEdit":
            deny("notebook edits are not tag-only")
        case "Write":
            old = path.read_text(errors="replace") if path.exists() else None
            if old is None or not core.only_tags_changed(old, inp.get("content", ""), prefix):
                deny(f"this Write to {raw} changes more than tag lines")
        case _:
            for e in inp.get("edits") or [inp]:
                if not core.only_tags_changed(
                    e.get("old_string", ""), e.get("new_string", ""), prefix
                ):
                    deny(f"this edit to {raw} changes more than tag lines")


def check_bash(inp: dict, cwd: str) -> None:
    repo = guarded_repo(Path(cwd))
    if repo is None:
        return
    cmd = inp.get("command", "")
    if re.match(r"\s*devtask\b", cmd) and not re.search(r"[;&|]", cmd):
        return
    for pattern, why in _BASH_DENY:
        if re.search(pattern, cmd):
            deny(f"{why} (`{cmd.strip()[:80]}`)")
    for target in _REDIRECT.findall(cmd):
        if target == "/dev/null":
            continue
        if inside(Path(cwd, target).expanduser(), repo.root):
            deny(f"shell redirection writes to {target}")


def session_start(data: dict) -> None:
    repo = guarded_repo(Path(data.get("cwd", ".")))
    if repo is None:
        return
    prefix = core.tag_prefix()
    emit(
        "SessionStart",
        additionalContext=(
            f"devtask guard is ON for {repo.root}. The user turned it on because "
            "they want to write the code in this repository themselves. Respect "
            "that: change source files only by adding or removing whole-line "
            f"`{prefix}<ID>` tag comments, by any tool or command. Describe "
            "changes in task files under `devtask path` instead, and if a task "
            "seems to need a code edit, ask the user. Use /devtask:plan to "
            "plan, /devtask:ask to talk through one tag, and /devtask:review "
            "to review the user's implementation. "
            "Write explanations in plain language (ISO 24495-1): give readers "
            "what they need, put the main point first under clear headings, "
            "use short active sentences with familiar words, and make each "
            "instruction something the reader can act on."
        ),
    )


def main() -> None:
    data = json.load(sys.stdin)
    inp = data.get("tool_input") or {}
    cwd = data.get("cwd") or "."
    match sys.argv[1:], data.get("tool_name"):
        case ["session-start"], _:
            session_start(data)
        case _, "Bash":
            check_bash(inp, cwd)
        case _, str(tool) if tool in FILE_TOOLS:
            check_file_tool(tool, inp, cwd)

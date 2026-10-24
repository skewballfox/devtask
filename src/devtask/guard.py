"""Claude Code hook: in a guarded repo or task, Claude may only add or remove tag lines.

The guard applies to a whole repo after `devtask init` or `devtask guard on`,
and to one task when random devtask mode picks it (see `devtask.mode`).

`session-start` tells Claude the guard is on and which policies apply.
`pre-tool-use` denies Bash commands an enabled policy forbids (see
`devtask.policy`). Under the guard it also denies file edits that change
anything but tag-only lines, and Bash commands that look like they write to
the repo. The Bash check is a heuristic; the git hook is the backstop.
It also rolls for random devtask mode (`on_edit`, or a Claude Code worktree)
and has the user approve devtask commands that turn devtask off.
`post-tool-use` rolls for branches a tool call created (`on_branch`).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath

from devtask import core, mode, policy, settings

FILE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
BRANCH_TOOLS = {"Bash", "EnterWorktree"}

# Commands that write to the working tree or history. Moving between branches
# (checkout, switch, pull, gh pr checkout) stays allowed for reading PRs. The
# flag says whether the command edits files, which starts a task for `on_edit`;
# committing or pushing work does not.
_BASH_DENY = [
    (r"\bsed\b[^|;&]*\s-[A-Za-z]*i", "sed -i edits files in place", True),
    (r"\bperl\b[^|;&]*\s-[A-Za-z]*i", "perl -i edits files in place", True),
    (r"\btee\b", "tee writes files", True),
    (r"\b(rm|mv|cp|touch|truncate|patch|dd|ln|install)\b", "file-changing command", True),
    (
        (
            r"\bgit\s+(add|commit|apply|am|stash|reset|restore|merge|rebase|cherry-pick"
            r"|revert|rm|mv|clean|push|checkout\s+(--|\.)\s*)"
        ),
        "git command that changes files or history",
        False,
    ),
    (
        (
            r"\b(cargo\s+(fmt|fix|clippy\s+[^|;&]*--fix)|rustfmt|gofmt\s+-w|black|isort"
            r"|ruff\s+(format|check\s+[^|;&]*--fix)|prettier\s+[^|;&]*--write"
            r"|eslint\s+[^|;&]*--fix|clang-format\s+[^|;&]*-i)\b"
        ),
        "formatter or auto-fixer",
        True,
    ),
]
_REDIRECT = re.compile(r"(?<![0-9&<>])>>?\s*(?!&)([^\s;|&)]+)")

# devtask commands that switch devtask off for a task or a repo. The user
# approves each one, so Claude can't opt out of what the user asked for.
_NEEDS_USER = re.compile(
    r"\bdevtask\s+(?:skip\b|guard\s+off\b|uninstall\b|policy\s+rm\b|reviewed\b|config\s+\S+\s+\S"
    r"|init\b[^;&|]*\s(?:--randomize|-s\b|--strategy))"
)


def emit(event: str, **fields: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def decide(decision: str, reason: str) -> None:
    emit("PreToolUse", permissionDecision=decision, permissionDecisionReason=reason)
    sys.exit(0)


def deny(reason: str, task_key: str | None) -> None:
    way_out = (
        f"Run `devtask skip {task_key}` only if the user tells you to."
        if task_key
        else "Run `devtask guard off` only if the user tells you to."
    )
    decide(
        "deny",
        f"devtask guard: {reason}. The user wants to write the code for "
        f"{'this task' if task_key else 'this repository'} themselves. Changing "
        "it beyond whole-line tag comments goes against what they asked for, "
        "including through a different tool or command. Put the change in the "
        f"task file as a description, or ask the user. {way_out}",
    )


def activate(key: str) -> None:
    """Stop the tool call so Claude tells the user devtask mode just started."""
    decide("deny", mode.activation_message(key))


def repo_for(path: Path) -> core.Repo | None:
    root = core.repo_root(path)
    return core.Repo.at(root) if root else None


def guard_state(repo: core.Repo, session_id: str | None) -> tuple[bool, str | None]:
    """Whether the guard applies, and the task key when random mode is why."""
    if repo.guard_enabled():
        return True, None
    key = mode.active_key(repo, repo.root, session_id)
    return key is not None, key


def inside(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not rel.parts or rel.parts[0] != ".git"


def relative(path: Path, root: Path) -> PurePosixPath:
    return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix())


def roll_before_tool(repo: core.Repo, session_id: str | None, edits_code: bool) -> None:
    """Roll for the current task if this tool call starts it; activate on a hit."""
    if settings.randomize(repo) <= 0 or repo.guard_enabled():
        return
    match settings.strategy(repo):
        case "on_branch":
            key = mode.roll_claude_worktree(repo)
        case _ if edits_code:
            key = mode.task_key(repo.root, session_id)
            key = key if key and mode.roll(repo, key, "on_edit") else None
        case _:
            key = None
    if key:
        activate(key)


def check_file_tool(tool: str, inp: dict, cwd: str, session_id: str | None) -> None:
    raw = inp.get("file_path") or inp.get("notebook_path")
    if not raw:
        return
    path = Path(cwd, raw)
    repo = repo_for(path)
    if repo is None or not inside(path, repo.root):
        return
    patterns = [p for p, _ in settings.ignore_patterns(repo)]
    roll_before_tool(repo, session_id, not settings.ignored(relative(path, repo.root), patterns))
    guarded, task_key = guard_state(repo, session_id)
    if not guarded:
        return
    prefix = core.tag_prefix()
    match tool:
        case "NotebookEdit":
            deny("notebook edits are not tag-only", task_key)
        case "Write":
            old = path.read_text(errors="replace") if path.exists() else None
            if old is None or not core.only_tags_changed(old, inp.get("content", ""), prefix):
                deny(f"this Write to {raw} changes more than tag lines", task_key)
        case _:
            for e in inp.get("edits") or [inp]:
                if not core.only_tags_changed(
                    e.get("old_string", ""), e.get("new_string", ""), prefix
                ):
                    deny(f"this edit to {raw} changes more than tag lines", task_key)


def check_bash(inp: dict, cwd: str, session_id: str | None) -> None:
    cmd = inp.get("command", "")
    if _NEEDS_USER.search(cmd):
        decide(
            "ask",
            "This devtask command turns devtask off for a task or changes random "
            "devtask mode. Only the user may decide that.",
        )
    repo = repo_for(Path(cwd))
    if repo is None:
        return
    if hit := policy.denied(repo, cmd):
        name, why = hit
        decide(
            "deny",
            f"devtask policy {name}: this command {why} (`{cmd.strip()[:80]}`). The user "
            f"turned this policy on so that they do this themselves; don't work around it "
            f"with another tool or command. Ask the user instead. Run "
            f"`devtask policy rm {name}` only if the user tells you to.",
        )
    if re.match(r"\s*devtask\b", cmd) and not re.search(r"[;&|]", cmd):
        return
    writes = [(why, edits) for pattern, why, edits in _BASH_DENY if re.search(pattern, cmd)]
    patterns = [p for p, _ in settings.ignore_patterns(repo)]
    for target in _REDIRECT.findall(cmd):
        path = Path(cwd, target).expanduser()
        if target != "/dev/null" and inside(path, repo.root):
            code = not settings.ignored(relative(path, repo.root), patterns)
            writes.append((f"shell redirection writes to {target}", code))
    roll_before_tool(repo, session_id, any(edits for _, edits in writes))
    guarded, task_key = guard_state(repo, session_id)
    if guarded and writes:
        deny(f"{writes[0][0]} (`{cmd.strip()[:80]}`)", task_key)


def pre_tool_use(data: dict) -> None:
    inp = data.get("tool_input") or {}
    cwd = data.get("cwd") or "."
    session_id = data.get("session_id")
    tool = data.get("tool_name")
    if tool in BRANCH_TOOLS and (tool_use_id := data.get("tool_use_id")):
        repo = repo_for(Path(cwd))
        if repo and settings.randomize(repo) > 0 and settings.strategy(repo) == "on_branch":
            mode.save_branches(repo, tool_use_id)
    match tool:
        case "Bash":
            check_bash(inp, cwd, session_id)
        case str(name) if name in FILE_TOOLS:
            check_file_tool(name, inp, cwd, session_id)


def post_tool_use(data: dict) -> None:
    tool_use_id = data.get("tool_use_id")
    repo = repo_for(Path(data.get("cwd") or "."))
    if repo is None or not tool_use_id or settings.strategy(repo) != "on_branch":
        return
    hits = mode.roll_new_branches(repo, tool_use_id)
    if hits:
        emit("PostToolUse", additionalContext="\n\n".join(map(mode.activation_message, hits)))


def guard_rules(prefix: str) -> str:
    return (
        "change source files only by adding or removing whole-line "
        f"`{prefix}<ID>` tag comments, by any tool or command. Describe "
        "changes in task files under `devtask path` instead, and if a task "
        "seems to need a code edit, ask the user. Use /devtask:plan to "
        "plan, /devtask:ask to talk through one tag, and /devtask:review "
        "to review the user's implementation. "
        "Write explanations in plain language (ISO 24495-1): give readers "
        "what they need, put the main point first under clear headings, "
        "use short active sentences with familiar words, and make each "
        "instruction something the reader can act on."
    )


def session_start(data: dict) -> None:
    repo = repo_for(Path(data.get("cwd", ".")))
    if repo is None:
        return
    context = guard_context(repo, data.get("session_id"))
    policies = policy.session_context(repo)
    text = "\n\n".join(part for part in (context, policies) if part)
    if text:
        emit("SessionStart", additionalContext=text)


def guard_context(repo: core.Repo, session_id: str | None) -> str:
    """What Claude needs to know at session start about the edit guard."""
    prefix = core.tag_prefix()
    if (
        settings.randomize(repo) > 0
        and settings.strategy(repo) == "on_branch"
        and not repo.guard_enabled()
        and (key := mode.roll_claude_worktree(repo))
    ):
        return mode.activation_message(key)
    guarded, task_key = guard_state(repo, session_id)
    if not guarded:
        return ""
    if task_key:
        intro = (
            f"devtask mode is ON for the current task ({mode.describe(task_key)}). "
            "A random roll picked it, so the user writes the code. If the user "
            f"opts out of devtask mode for this task, run `devtask skip {task_key}`. "
            "Until then, "
        )
    else:
        intro = (
            f"devtask guard is ON for {repo.root}. The user turned it on because "
            "they want to write the code in this repository themselves. Respect "
            "that: "
        )
    return intro + guard_rules(prefix)


def main() -> None:
    data = json.load(sys.stdin)
    match sys.argv[1:]:
        case ["session-start"]:
            session_start(data)
        case ["post-tool-use"]:
            post_tool_use(data)
        case _:
            pre_tool_use(data)

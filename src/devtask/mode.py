"""Random devtask mode: some new tasks are handed back to the user to write.

With `randomize` above 0, devtask rolls once per task. On a hit the task is
in devtask mode: the guard applies to it as if `devtask guard on` had run, and
Claude must tell the user, who may skip devtask mode for that task.

A task is identified by its branch, or by the Claude session when the work
happens on the default branch or a detached HEAD, where many unrelated tasks
share one branch. `randomization_strategy` picks when the roll happens:
`on_branch` when Claude creates a branch, `on_edit` at its first code edit.
Every roll is recorded in the state directory's rolls.json, keyed by
`branch:<name>` or `session:<id>`, so no task is rolled twice.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from devtask import core, settings


def rolls_file(repo: core.Repo) -> Path:
    return repo.state_dir / "rolls.json"


def load_rolls(repo: core.Repo) -> dict[str, dict]:
    try:
        return json.loads(rolls_file(repo).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_rolls(repo: core.Repo, rolls: dict[str, dict]) -> None:
    # Hooks may run in parallel; replace the file atomically.
    path = rolls_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rolls-")
    with os.fdopen(fd, "w") as f:
        json.dump(rolls, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def current_branch(root: Path) -> str | None:
    """The branch checked out in the worktree at `root`, or None when detached."""
    return core.git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=root)


def default_branches(root: Path) -> set[str]:
    names = {"main", "master"}
    if remote_head := core.git(
        "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", cwd=root
    ):
        names.add(remote_head.partition("/")[2])
    if init_default := core.git("config", "--get", "init.defaultBranch", cwd=root):
        names.add(init_default)
    return names


def task_key(root: Path, session_id: str | None) -> str | None:
    """The key of the task that work in the worktree at `root` belongs to."""
    branch = current_branch(root)
    if branch and branch not in default_branches(root):
        return f"branch:{branch}"
    return f"session:{session_id}" if session_id else None


def active(record: dict | None) -> bool:
    return bool(record and record.get("hit") and not record.get("skipped"))


def active_key(repo: core.Repo, root: Path, session_id: str | None) -> str | None:
    """The key of the record putting this work in devtask mode, if any.

    Both the branch and the session count, so a task that started on the
    default branch stays in devtask mode when Claude moves it to a branch.
    """
    rolls = load_rolls(repo)
    keys = []
    if branch := current_branch(root):
        keys.append(f"branch:{branch}")
    if session_id:
        keys.append(f"session:{session_id}")
    return next((k for k in keys if active(rolls.get(k))), None)


def roll(repo: core.Repo, key: str, how: str, fresh: bool = False) -> bool:
    """Roll for `key` once and record it. Returns True on a new hit.

    `fresh` replaces an earlier record: the branch was just created, so any
    record under its name belonged to a deleted branch.
    """
    rolls = load_rolls(repo)
    if key in rolls and not fresh:
        return False
    p = settings.randomize(repo)
    hit = random.random() < p
    rolls[key] = {
        "p": p,
        "strategy": how,
        "hit": hit,
        "skipped": False,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    save_rolls(repo, rolls)
    return hit


def skip(repo: core.Repo, key: str) -> None:
    rolls = load_rolls(repo)
    rolls[key]["skipped"] = True
    save_rolls(repo, rolls)


def describe(key: str) -> str:
    kind, _, name = key.partition(":")
    return f"branch `{name}`" if kind == "branch" else "this session"


def activation_message(key: str) -> str:
    """What Claude is told when a roll puts its task in devtask mode."""
    return (
        f"devtask mode is now ON for this task ({describe(key)}). The user set "
        "devtask to hand some new tasks back to them at random: they write the "
        "code, and you plan it as tag comments and a task file. Before you do "
        "anything else, tell the user plainly that devtask mode was turned on "
        "for this task, and that they can opt out of it for this task only "
        "(random devtask mode stays on for later tasks). Do not opt out on your "
        f"own. If the user opts out, run `devtask skip {key}`; they will be "
        "asked to approve it. Otherwise, plan the change with /devtask:plan. "
        "Until then, edits to the repository are limited to whole-line "
        f"`{core.tag_prefix()}<ID>` tag comments. If you are a subagent, stop "
        "and report this to the main agent so it can tell the user."
    )


# on_branch: a branch is new when it appears during one tool call. PreToolUse
# saves the branch list under the call's tool_use_id; PostToolUse compares.
# Worktrees Claude Code makes without a tool call (`claude --worktree`, subagent
# isolation) live under .claude/worktrees/; the first hook to see one with an
# unrolled branch rolls for it.

PENDING_MAX_AGE = 3600  # seconds; snapshots from calls that never finished


def branches(root: Path) -> set[str]:
    out = core.git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=root)
    return set(out.splitlines()) if out else set()


def pending_file(repo: core.Repo, tool_use_id: str) -> Path:
    safe = "".join(c for c in tool_use_id if c.isalnum() or c in "-_")
    return repo.state_dir / "pending" / f"{safe}.json"


def save_branches(repo: core.Repo, tool_use_id: str) -> None:
    path = pending_file(repo, tool_use_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in path.parent.glob("*.json"):
        if now - old.stat().st_mtime > PENDING_MAX_AGE:
            old.unlink(missing_ok=True)
    path.write_text(json.dumps(sorted(branches(repo.root))))


def roll_new_branches(repo: core.Repo, tool_use_id: str) -> list[str]:
    """Roll for each branch created since `save_branches`. Returns keys that hit."""
    path = pending_file(repo, tool_use_id)
    try:
        before = set(json.loads(path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    path.unlink(missing_ok=True)
    skip_names = default_branches(repo.root)
    keys = [f"branch:{b}" for b in sorted(branches(repo.root) - before) if b not in skip_names]
    return [key for key in keys if roll(repo, key, "on_branch", fresh=True)]


def in_claude_worktree(root: Path) -> bool:
    return root.parent.name == "worktrees" and root.parent.parent.name == ".claude"


def roll_claude_worktree(repo: core.Repo) -> str | None:
    """Roll for a Claude Code worktree's branch once. Returns its key on a hit."""
    if not in_claude_worktree(repo.root):
        return None
    branch = current_branch(repo.root)
    if not branch or branch in default_branches(repo.root):
        return None
    key = f"branch:{branch}"
    return key if roll(repo, key, "on_branch") else None

"""Policies: toggles that limit what a Claude Code session may do in a repo.

`devtask policy add <name>` turns one on for the repo (or with --global for
every repo); `devtask policy rm <name>` turns it off. A policy can deny Bash
commands (checked by the Claude Code hook in `devtask.guard`), refuse git
operations that a Claude session runs (checked by devtask's git hooks, which
see CLAUDECODE=1 in the environment), and tell Claude about itself at
session start. They are separate from the edit guard and apply whether or not
it is on.

Policies are stored as a `policies` list in the global and project config.
The effective set is the union of both.

review-gate is checked by the pre-commit hook for every commit, not only a
Claude session's: each staged file must match the content /devtask:review
saw, as recorded by `devtask reviewed`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devtask import core, settings

# git, then any global options, then the subcommand.
_GIT = r"\bgit(?:\s+(?:-[Cc]\s+\S+|--[\w-]+(?:=\S+)?))*\s+"


@dataclass(frozen=True)
class Policy:
    help: str
    context: str  # told to Claude at session start
    deny: tuple[tuple[str, str], ...] = ()  # (regex on a Bash command, why)
    git_events: tuple[str, ...] = ()  # git hooks that refuse Claude sessions


POLICIES: dict[str, Policy] = {
    "commit-guard": Policy(
        "Claude may not create commits",
        "Do not create commits (git commit, merge, cherry-pick, revert, am). "
        "Leave committing to the user; tell them when the work is ready.",
        ((_GIT + r"(?:commit|merge|cherry-pick|revert|am)\b", "creates a commit"),),
        ("pre-commit",),
    ),
    "push-guard": Policy(
        "Claude may not push or publish (git push, gh pr/issue/release writes)",
        "Do not push or publish anything: no git push, and no gh commands that "
        "create, edit, comment on, review, merge or close pull requests, issues "
        "or releases. Draft the text and give it to the user instead.",
        (
            (_GIT + r"push\b", "pushes to a remote"),
            (
                (
                    r"\bgh\s+(?:pr|issue)\s+(?:create|edit|comment|review|merge|close|reopen"
                    r"|ready|lock|delete)\b"
                ),
                "publishes to GitHub",
            ),
            (r"\bgh\s+release\s+(?:create|edit|delete|upload)\b", "publishes a release"),
        ),
        ("pre-push",),
    ),
    "history-guard": Policy(
        "Claude may not rewrite history or discard work",
        "Do not run git commands that rewrite history or throw away work "
        "(reset --hard, rebase, force pushes, checkout/restore over changes, "
        "clean, stash drop/clear, branch -D). Ask the user to do it.",
        (
            (_GIT + r"reset\b[^;&|]*--hard\b", "discards changes"),
            (_GIT + r"(?:rebase|filter-branch|filter-repo)\b", "rewrites history"),
            (_GIT + r"push\b[^;&|]*(?:\s-f\b|--force)", "force-pushes"),
            (_GIT + r"checkout\b[^;&|]*\s(?:--|\.)(?:\s|$)", "overwrites working-tree changes"),
            (_GIT + r"restore\b(?![^;&|]*--staged)", "overwrites working-tree changes"),
            (_GIT + r"clean\b[^;&|]*\s-[A-Za-z]*f", "deletes untracked files"),
            (_GIT + r"stash\s+(?:drop|clear)\b", "deletes stashed work"),
            (_GIT + r"branch\b[^;&|]*\s(?:-D\b|--delete\s+--force)", "deletes a branch"),
            (_GIT + r"(?:reflog\s+expire|update-ref\s+-d|gc\b[^;&|]*--prune)", "drops history"),
        ),
    ),
    "deps-guard": Policy(
        "Claude may not add or install dependencies",
        "Do not add, install or upgrade dependencies (npm/pnpm/yarn/bun add, "
        "cargo add, uv add, pip install, go get and the like). If the work needs "
        "a new dependency, name it and let the user decide.",
        (
            (r"\b(?:npm|pnpm|bun)\s+(?:add|install|i)\s+[^-\s]", "adds a dependency"),
            (r"\b(?:yarn|uv|poetry|cargo|pdm|rye|bundle)\s+add\b", "adds a dependency"),
            (r"\b(?:uv\s+)?pip3?\s+install\s+[^-\s]", "installs a package"),
            (r"\bgo\s+get\b", "adds a dependency"),
            (r"\bgem\s+install\b", "installs a package"),
            (r"\bcomposer\s+require\b", "adds a dependency"),
            (r"\bdotnet\s+add\b[^;&|]*\bpackage\b", "adds a dependency"),
        ),
    ),
    "review-gate": Policy(
        "commits need a /devtask:review of exactly the staged changes",
        "Commits are refused until /devtask:review has reviewed the exact content "
        "being committed; any change after a review needs a new one. When the user "
        "is ready to commit, offer to run /devtask:review. Never run "
        "`devtask reviewed` except as the last step of /devtask:review.",
    ),
    "no-code-in-chat": Policy(
        "Claude explains and hints, but doesn't write implementation code in replies",
        "Do not write implementation code for the user in your replies either. "
        "Explain, point to the relevant code, ask guiding questions and give "
        "hints; quoting existing code is fine. The user writes the new code.",
    ),
}


def configured(path_config: dict) -> list[str]:
    value = path_config.get("policies", [])
    return [p for p in value if isinstance(p, str)] if isinstance(value, list) else []


def enabled(repo: core.Repo | None) -> list[tuple[str, str]]:
    """The policies on for `repo`, as (name, source), in POLICIES order."""
    glob = configured(core.read_toml(core.global_config_file()))
    project = configured(core.read_toml(repo.config_file)) if repo else []
    return [
        (name, "project" if name in project else "global")
        for name in POLICIES
        if name in project or name in glob
    ]


def set_enabled(path: Path, names: list[str], on: bool) -> list[str]:
    """Add or remove `names` in the config file at `path`. Returns the changed ones."""
    current = configured(core.read_toml(path))
    if on:
        changed = [n for n in names if n not in current]
        current += changed
    else:
        changed = [n for n in names if n in current]
        current = [n for n in current if n not in names]
    if changed:
        settings.write_key(path, "policies", current or None)
    return changed


def denied(repo: core.Repo | None, cmd: str) -> tuple[str, str] | None:
    """The first enabled policy that denies this Bash command, as (name, why)."""
    for name, _ in enabled(repo):
        for pattern, why in POLICIES[name].deny:
            if re.search(pattern, cmd):
                return name, why
    return None


def git_hook_refusal(repo: core.Repo, event: str) -> str | None:
    """Why a git hook should refuse `event`, when a Claude session runs it."""
    if os.environ.get("CLAUDECODE") != "1":
        return None
    for name, _ in enabled(repo):
        if event in POLICIES[name].git_events:
            return name
    return None


def session_context(repo: core.Repo | None) -> str:
    names = [name for name, _ in enabled(repo)]
    if not names:
        return ""
    lines = [f"- {name}: {POLICIES[name].context}" for name in names]
    return (
        "The user turned on these devtask policies for this repository. Follow "
        "them, by any tool or command, and don't work around them:\n" + "\n".join(lines)
    )


# review-gate. `devtask reviewed` records the blob hash of every file that
# differs from HEAD; the pre-commit hook then requires each staged file's blob
# to match. Paths matching randomize_ignore (documentation) are exempt.

ZERO = "0" * 40


def reviews_file(repo: core.Repo) -> Path:
    return repo.state_dir / "reviews.json"


def load_reviews(repo: core.Repo) -> dict:
    try:
        data = json.loads(reviews_file(repo).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def changed_files(root: Path) -> dict[str, str]:
    """Every path whose working-tree content differs from HEAD, untracked
    files included, mapped to its blob hash, or "deleted"."""
    out = core.git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=root, strip=False)
    fields = (out or "").split("\0")
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" and i < len(fields):
            paths.append(fields[i])  # the source path, gone for a rename
            i += 1
    present = [p for p in paths if (root / p).is_file()]
    hashes = core.git("hash-object", "--", *present, cwd=root) if present else ""
    files = {p: "deleted" for p in paths}
    files.update(zip(present, (hashes or "").split(), strict=False))
    return files


def record_review(repo: core.Repo, task: str | None) -> dict[str, str]:
    """Record the current changes as reviewed and return them. Earlier records
    for paths that no longer differ from HEAD are dropped."""
    files = changed_files(repo.root)
    reviewed = load_reviews(repo).get("files", {})
    kept = {p: h for p, h in reviewed.items() if p in files}
    kept.update(files)
    data = {"files": kept, "task": task, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    repo.state_dir.mkdir(parents=True, exist_ok=True)
    reviews_file(repo).write_text(json.dumps(data, indent=2) + "\n")
    return files


def unreviewed_staged(repo: core.Repo) -> list[str]:
    """Staged paths whose content /devtask:review hasn't seen."""
    out = (
        core.git("diff", "--cached", "--raw", "--no-abbrev", "-z", "--no-renames", cwd=repo.root)
        or ""
    )
    fields = out.split("\0")
    reviewed = load_reviews(repo).get("files", {})
    patterns = [p for p, _ in settings.ignore_patterns(repo)]
    missing = []
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        parts = meta.split()
        if len(parts) < 5:
            continue
        blob = "deleted" if parts[3].strip("0") == "" else parts[3]
        if settings.ignored(Path(path), patterns):
            continue
        if reviewed.get(path) != blob:
            missing.append(path)
    return missing

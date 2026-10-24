"""Shared logic for the devtask CLI and the Claude Code guard hook.

A devtask is a planned change, marked in the working tree by a whole-line
comment holding a tag (`// #DEVTASK_pr123-1`) and described in a task file
kept outside the repository. Nothing here writes code; it only finds,
validates and removes tag lines and locates devtask's files.

In the working tree, `devtask init` only writes CLAUDE.local.md and
.claude/settings.local.json (see LOCAL_FILES), listing each one in the repo's
info/exclude. In .git it adds its git hook entries to the repo's config. Other
files follow the XDG Base Directory spec, honoring XDG_CONFIG_HOME,
XDG_DATA_HOME and XDG_STATE_HOME:

    $XDG_CONFIG_HOME/devtask/config.toml           global settings
    $XDG_DATA_HOME/devtask/repos/<repo>/config.toml project settings (override global)
    $XDG_DATA_HOME/devtask/repos/<repo>/rules.md    rules CLAUDE.local.md imports
    $XDG_DATA_HOME/devtask/repos/<repo>/tasks/      task files
    $XDG_STATE_HOME/devtask/repos/<repo>/           guard flag, rolls, repo info
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

APP = "devtask"
DEFAULT_TAG = "#DEVTASK_"
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]*"

# Comment openers and closers a tag line may use, so the tag stays a valid
# comment in most languages while the planned change is being written.
_LEADERS = r"(?://+|#+|--|;+|%+|/\*+|<!--|\{-|\(\*|'|\"|REM\b|!)"
_CLOSERS = r"(?:\*+/|-->|-\}|\*\))"


def git(*args: str, cwd: Path | None = None, strip: bool = True) -> str | None:
    """Run git and return stdout (stripped unless `strip` is false, for output
    whose leading whitespace matters), or None when it fails."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None
    return out.stdout.strip() if strip else out.stdout


def xdg_base(var: str, default: Path) -> Path:
    # The spec says relative paths in these variables are invalid and ignored.
    value = os.environ.get(var)
    if value and Path(value).is_absolute():
        return Path(value)
    return default


def config_dir() -> Path:
    return xdg_base("XDG_CONFIG_HOME", Path.home() / ".config") / APP


def data_home() -> Path:
    return xdg_base("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP


def state_home() -> Path:
    return xdg_base("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP


def global_config_file() -> Path:
    return config_dir() / "config.toml"


def read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load_config(repo: Repo | None = None) -> dict:
    """The global config with the repo's project config, if any, on top."""
    config = read_toml(global_config_file())
    if repo is not None:
        config |= read_toml(repo.config_file)
    return config


def tag_prefix() -> str:
    """The tag prefix: `tag` in the global config.toml, else the default."""
    return load_config().get("tag") or DEFAULT_TAG


def tag_line_re(prefix: str, ids: str = ID_PATTERN) -> re.Pattern[str]:
    """Matches a line that holds nothing but a commented tag."""
    return re.compile(rf"^\s*(?:{_LEADERS}\s*)?{re.escape(prefix)}(?P<id>{ids})\s*{_CLOSERS}?\s*$")


def tag_any_re(prefix: str) -> re.Pattern[str]:
    """Matches a tag anywhere in a line."""
    return re.compile(rf"{re.escape(prefix)}(?P<id>{ID_PATTERN})")


def strip_tag_lines(text: str, prefix: str) -> list[str]:
    """The lines of `text` with every tag-only line removed."""
    line_re = tag_line_re(prefix)
    return [line for line in text.splitlines() if not line_re.match(line)]


def only_tags_changed(old: str, new: str, prefix: str) -> bool:
    """True when `old` and `new` differ by nothing but tag-only lines."""
    return strip_tag_lines(old, prefix) == strip_tag_lines(new, prefix)


def repo_root(path: Path) -> Path | None:
    """The worktree root containing `path`, which need not exist yet."""
    p = path.expanduser().absolute()
    while not p.exists() and p != p.parent:
        p = p.parent
    if p.is_file():
        p = p.parent
    top = git("rev-parse", "--show-toplevel", cwd=p)
    return Path(top) if top else None


@dataclass(frozen=True)
class Repo:
    """A repository's devtask locations, shared by all of its worktrees."""

    root: Path
    key: str

    @classmethod
    def at(cls, root: Path) -> Repo:
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
        key_path = Path(common) if common else root
        if key_path.name == ".git":
            key_path = key_path.parent
        digest = hashlib.sha1(str(key_path).encode()).hexdigest()[:8]
        return cls(root, f"{key_path.name}-{digest}")

    @property
    def data_dir(self) -> Path:
        return data_home() / "repos" / self.key

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def config_file(self) -> Path:
        """Project settings, which take precedence over the global config."""
        return self.data_dir / "config.toml"

    @property
    def state_dir(self) -> Path:
        return state_home() / "repos" / self.key

    @property
    def init_marker(self) -> Path:
        return self.state_dir / "initialized"

    def initialized(self) -> bool:
        """True once `devtask init` has run here, or the repo guard is on."""
        return self.init_marker.exists() or self.guard_enabled()

    @property
    def guard_file(self) -> Path:
        return self.state_dir / "guard"

    def guard_enabled(self) -> bool:
        return self.guard_file.exists()

    def ensure(self) -> None:
        """Create this repo's data and state directories."""
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        meta = self.state_dir / "repo.json"
        if not meta.exists():
            origin = git("remote", "get-url", "origin", cwd=self.root)
            meta.write_text(json.dumps({"path": str(self.root), "origin": origin}, indent=2) + "\n")

    def task_files(self) -> list[Path]:
        return sorted(self.tasks_dir.glob("*.md"))

    @property
    def rules_file(self) -> Path:
        """Rules for Claude in this repo, imported by its CLAUDE.local.md."""
        return self.data_dir / "rules.md"

    @property
    def main_root(self) -> Path:
        """The main checkout. Claude Code reads its .claude/settings.local.json
        in linked worktrees too. A bare repo has none, so this is `root` there."""
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=self.root)
        if common and Path(common).name == ".git":
            return Path(common).parent
        return self.root

    @property
    def exclude_file(self) -> Path:
        """The repo's untracked ignore list, shared by all of its worktrees."""
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=self.root)
        return Path(common or self.root / ".git") / "info" / "exclude"


# Files `devtask init` may write to a worktree, relative to its root.
LOCAL_FILES = ("CLAUDE.local.md", ".claude/settings.local.json")
EXCLUDE_HEADER = "# devtask"


def set_excluded(repo: Repo, rel_paths: list[str], on: bool) -> list[str]:
    """Add (`on`) or remove a `/<path>` line for each of these files in the
    repo's info/exclude. Lines live in a block under devtask's header and name
    single files, never a directory, so files a project adds next to them
    (say, in .claude/) stay visible to git. Returns the paths that changed."""
    path = repo.exclude_file
    lines = path.read_text().splitlines() if path.exists() else []
    ours: list[str] = []
    start = len(lines)
    if EXCLUDE_HEADER in lines:
        start = end = lines.index(EXCLUDE_HEADER)
        end += 1
        while end < len(lines) and lines[end].removeprefix("/") in LOCAL_FILES:
            ours.append(lines[end].removeprefix("/"))
            end += 1
        del lines[start:end]
    if on:
        changed = [p for p in rel_paths if p not in ours and f"/{p}" not in lines]
        ours += changed
    else:
        changed = [p for p in ours if p in rel_paths]
        ours = [p for p in ours if p not in rel_paths]
    if changed:
        if ours:
            lines[start:start] = [EXCLUDE_HEADER, *(f"/{p}" for p in sorted(ours))]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{line}\n" for line in lines))
    return sorted(changed)


def section_re(prefix: str) -> re.Pattern[str]:
    """Matches a task-file heading that introduces one tag's section."""
    return re.compile(
        rf"^##\s+.*?{re.escape(prefix)}(?P<id>{ID_PATTERN})(?:\s*:\s*(?P<title>.*))?$",
        re.MULTILINE,
    )


@dataclass(frozen=True)
class Section:
    task_file: Path
    title: str
    text: str


def read_sections(repo: Repo, prefix: str) -> dict[str, Section]:
    """Every documented tag ID mapped to its section of a task file."""
    sections: dict[str, Section] = {}
    head_re = section_re(prefix)
    for task_file in repo.task_files():
        text = task_file.read_text()
        for m in head_re.finditer(text):
            nxt = re.search(r"^##?\s", text[m.end() :], re.MULTILINE)
            end = m.end() + nxt.start() if nxt else len(text)
            sections[m.group("id")] = Section(
                task_file,
                (m.group("title") or "").strip(),
                text[m.start() : end].rstrip() + "\n",
            )
    return sections


def task_title(task_file: Path) -> str:
    """The title from a task file's `# name: title` heading."""
    for line in task_file.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].partition(":")[2].strip()
    return ""


def find_tags(root: Path, prefix: str) -> list[tuple[str, Path, int, str]]:
    """Every tag in the working tree as (id, repo-relative file, line, text)."""
    out = git("grep", "-n", "-I", "--untracked", "--no-color", "-F", "-e", prefix, cwd=root)
    if not out:
        return []
    any_re = tag_any_re(prefix)
    found = []
    for row in out.splitlines():
        file, lineno, line = row.split(":", 2)
        found.extend((m.group("id"), Path(file), int(lineno), line) for m in any_re.finditer(line))
    return found


def added_tag_lines(patch: str, prefix: str) -> list[str]:
    """`file:line: text` for each added line of a -U0 patch holding a tag."""
    hits = []
    file, lineno = "", 0
    for line in patch.splitlines():
        match line.split(" ", 3):
            case ["+++", path, *_]:
                file = path.removeprefix("b/")
            case ["@@", _, new_range, *_]:
                lineno = int(new_range.lstrip("+").split(",")[0])
            case [first, *_] if first.startswith("+"):
                if prefix in line:
                    hits.append(f"{file}:{lineno}: {line[1:].strip()}")
                lineno += 1
    return hits

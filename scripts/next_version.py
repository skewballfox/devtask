"""Work out the next release version from two signals and take the larger.

1. Commit messages since the last `v*` tag, read as conventional commits:
   `feat!:` or a `BREAKING CHANGE:` footer is major, `feat:` is minor, and
   anything else that changes the shipped tool is a patch. `ci`, `test`,
   `style`, `build` and `chore` commits don't count on their own.
2. devtask's public API, its command-line surface: every command, option,
   positional argument and choice, read from the argparse parser at the tag
   and at HEAD. Removing or tightening something is major, adding is minor.

Before 1.0.0 a major change bumps the minor version, per SemVer item 4.

    uv run scripts/next_version.py                  # report only
    uv run scripts/next_version.py --bump minor     # force a level
    uv run scripts/next_version.py --github         # also write Actions outputs

The surface check covers the CLI only. Changes to the config file, the tag
format or where files live need a `!` or `BREAKING CHANGE:` commit.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEVELS = ("none", "patch", "minor", "major")
QUIET_TYPES = {"ci", "test", "style", "build", "chore"}
SUBJECT_RE = re.compile(r"(?P<type>\w+)(?:\([^)]*\))?(?P<bang>!)?:\s")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)

# Run in a separate interpreter so the tagged source can be imported cleanly.
SURFACE_SNIPPET = """
import argparse, json
from devtask.cli import build_parser

sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
surface = {}
for name, parser in sub.choices.items():
    args, position = {}, 0
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        info = {
            "choices": sorted(map(str, action.choices)) if action.choices else None,
            "takes_value": action.nargs != 0,
        }
        if action.option_strings:
            for option in action.option_strings:
                args[option] = {**info, "required": action.required}
        else:
            args[f"<arg {position}>"] = {**info, "required": action.nargs not in ("?", "*")}
            position += 1
    surface[name] = args
print(json.dumps(surface))
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def last_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    except subprocess.CalledProcessError:
        return None


def commit_bump(since: str) -> tuple[str, list[str]]:
    """The bump the commits since `since` ask for, and why."""
    log = git("log", f"{since}..HEAD", "--format=%B%x00")
    level, reasons = "none", []
    for message in filter(None, (m.strip() for m in log.split("\0"))):
        subject = message.splitlines()[0]
        match SUBJECT_RE.match(subject), BREAKING_RE.search(message):
            case (m, _) if m and m["bang"]:
                found = "major"
            case (_, breaking) if breaking:
                found = "major"
            case (m, _) if m and m["type"] == "feat":
                found = "minor"
            case (m, _) if m and m["type"] in QUIET_TYPES:
                found = "none"
            case _:
                found = "patch"
        if found != "none":
            reasons.append(f"{found}: {subject}")
        level = max(level, found, key=LEVELS.index)
    return level, reasons


def surface(src: Path) -> dict[str, dict[str, dict]]:
    out = subprocess.run(
        [sys.executable, "-c", SURFACE_SNIPPET],
        cwd=src,
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def surface_at(tag: str) -> dict[str, dict[str, dict]] | None:
    """The CLI surface at `tag`, or None if that tag's source can't be read."""
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", tag, "src"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter="data")
            return surface(Path(tmp) / "src")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def surface_bump(old: dict, new: dict) -> tuple[str, list[str]]:
    """The bump that the change from `old` to `new` CLI surface requires."""
    changes: list[tuple[str, str]] = []
    for cmd in old.keys() - new.keys():
        changes.append(("major", f"command `{cmd}` removed"))
    for cmd in new.keys() - old.keys():
        changes.append(("minor", f"command `{cmd}` added"))
    for cmd in old.keys() & new.keys():
        before, after = old[cmd], new[cmd]
        for arg in before.keys() - after.keys():
            changes.append(("major", f"`{cmd} {arg}` removed"))
        for arg in after.keys() - before.keys():
            level = "major" if after[arg]["required"] else "minor"
            changes.append((level, f"`{cmd} {arg}` added"))
        for arg in before.keys() & after.keys():
            was, now = before[arg], after[arg]
            if now["required"] and not was["required"]:
                changes.append(("major", f"`{cmd} {arg}` is now required"))
            if now["takes_value"] != was["takes_value"]:
                changes.append(("major", f"`{cmd} {arg}` changed whether it takes a value"))
            match was["choices"], now["choices"]:
                case [*a], [*b] if set(a) - set(b):
                    changes.append(
                        ("major", f"`{cmd} {arg}` lost choices {sorted(set(a) - set(b))}")
                    )
                case [*a], [*b] if set(b) - set(a):
                    changes.append(
                        ("minor", f"`{cmd} {arg}` gained choices {sorted(set(b) - set(a))}")
                    )
    level = max((lvl for lvl, _ in changes), default="none", key=LEVELS.index)
    return level, [f"{lvl}: {why}" for lvl, why in changes]


def bumped(version: str, level: str) -> str:
    major, minor, patch = map(int, version.split("."))
    match level:
        case "major" if major == 0:
            return f"0.{minor + 1}.0"
        case "major":
            return f"{major + 1}.0.0"
        case "minor":
            return f"{major}.{minor + 1}.0"
        case "patch":
            return f"{major}.{minor}.{patch + 1}"
        case _:
            return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--bump", choices=("auto", *LEVELS[1:]), default="auto", help="force a level"
    )
    parser.add_argument(
        "--github", action="store_true", help="write GITHUB_OUTPUT and GITHUB_STEP_SUMMARY"
    )
    args = parser.parse_args()

    current = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    tag = last_tag()
    lines: list[str] = []
    match tag, args.bump:
        case None, _:
            # First release: ship the version already in pyproject.toml.
            level, version = "initial", current
            lines.append(f"No release tag yet; the first release is v{current}.")
        case str(since), "auto":
            c_level, c_reasons = commit_bump(since)
            old = surface_at(since)
            if old is None:
                s_level, s_reasons = "none", ["could not read the CLI at the tag; skipped"]
            else:
                s_level, s_reasons = surface_bump(old, surface(ROOT / "src"))
            level = max(c_level, s_level, key=LEVELS.index)
            version = bumped(current, level)
            lines += [
                f"Since {since}: commits say **{c_level}**, CLI surface says **{s_level}**.",
                "",
                *(f"- commit {r}" for r in c_reasons),
                *(f"- CLI {r}" for r in s_reasons),
            ]
        case _, forced:
            level, version = forced, bumped(current, forced)
            lines.append(f"Bump forced to **{forced}**.")

    release = level != "none"
    headline = f"Next release: v{version} ({level})" if release else "Nothing to release."
    report = "\n".join([f"### {headline}", "", *lines]) + "\n"
    print(report, end="")

    if args.github:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as out:
            out.write(f"release={str(release).lower()}\nversion={version}\nlevel={level}\n")
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(report)


if __name__ == "__main__":
    main()

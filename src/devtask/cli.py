"""devtask: plan a change as in-code tags, with the plan kept outside the repo."""

from __future__ import annotations

import argparse
import functools
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from devtask import core

HOOK_EVENTS = ("pre-commit", "pre-push")

# Arguments whose values the fish completions fetch from `devtask _complete <kind>`.
COMPLETE: dict[argparse.Action, str] = {}

TASK_TEMPLATE = """# {task}: {title}

Source: <issue or PR link>

## Summary

<What the change is for, in two or three sentences.>

## Orientation

<The parts of the codebase this touches and how they fit together; where to
start reading.>

One section per tag; the heading must contain the tag, for example:

    ## {prefix}{task}-1: short title

    **Where:** path/to/file.rs, in `function_name`
    **What:** the change, described in prose (no ready-to-paste code)
    **Why:** the reason this spot needs it
    **Questions:** what to work out before writing it
    **Done when:** how to tell it works
"""


def die(msg: str) -> NoReturn:
    print(f"devtask: {msg}", file=sys.stderr)
    sys.exit(1)


def current_repo() -> core.Repo:
    root = core.repo_root(Path.cwd())
    if root is None:
        die("not inside a git repository")
    return core.Repo.at(root)


def cmd_init(_args: argparse.Namespace) -> None:
    repo = current_repo()
    ensure_setup()
    repo.ensure()
    repo.guard_file.touch()
    print(f"tasks: {repo.tasks_dir}")
    print(f"state: {repo.state_dir}")
    print("guard: on (Claude may only add or remove tag lines in this repo)")


def cmd_info(_args: argparse.Namespace) -> None:
    print(f"config: {core.config_dir() / 'config.toml'}")
    print(f"tag:    {core.tag_prefix()}")
    root = core.repo_root(Path.cwd())
    if root is None:
        print("repo:   (not inside a git repository)")
        return
    repo = core.Repo.at(root)
    print(f"repo:   {repo.root}")
    print(f"tasks:  {repo.tasks_dir}")
    print(f"state:  {repo.state_dir}")
    print(f"guard:  {'on' if repo.guard_enabled() else 'off'}")


def cmd_path(args: argparse.Namespace) -> None:
    repo = current_repo()
    repo.ensure()
    print(repo.state_dir if args.state else repo.tasks_dir)


def cmd_guard(args: argparse.Namespace) -> None:
    repo = current_repo()
    repo.ensure()
    match args.state:
        case "on":
            repo.guard_file.touch()
        case "off":
            repo.guard_file.unlink(missing_ok=True)
    print("guard:", "on" if repo.guard_enabled() else "off")


def cmd_new(args: argparse.Namespace) -> None:
    repo = current_repo()
    if not re.fullmatch(core.ID_PATTERN, args.task):
        die(f"task name {args.task!r} must match {core.ID_PATTERN}")
    repo.ensure()
    task_file = repo.tasks_dir / f"{args.task}.md"
    if not task_file.exists():
        task_file.write_text(
            TASK_TEMPLATE.format(
                task=args.task, title=args.title or "<title>", prefix=core.tag_prefix()
            )
        )
    print(task_file)


def known_ids(repo: core.Repo, prefix: str) -> set[str]:
    return set(core.read_sections(repo, prefix)) | {t[0] for t in core.find_tags(repo.root, prefix)}


def cmd_next_id(args: argparse.Namespace) -> None:
    repo = current_repo()
    prefix = core.tag_prefix()
    task_re = re.compile(rf"{re.escape(args.task)}-(\d+)")
    nums = [int(m[1]) for i in known_ids(repo, prefix) if (m := task_re.fullmatch(i))]
    print(f"{prefix}{args.task}-{max(nums, default=0) + 1}")


def cmd_scan(args: argparse.Namespace) -> None:
    repo = current_repo()
    prefix = core.tag_prefix()
    sections = core.read_sections(repo, prefix)
    line_re = core.tag_line_re(prefix)

    def wanted(tag_id: str) -> bool:
        return args.task is None or tag_id.startswith(f"{args.task}-")

    problems = 0
    seen = set()
    for tag_id, file, lineno, line in core.find_tags(repo.root, prefix):
        if not wanted(tag_id):
            continue
        seen.add(tag_id)
        notes = []
        if tag_id not in sections:
            notes.append("UNMAPPED")
        if not line_re.match(line):
            notes.append("NOT A TAG-ONLY LINE")
        problems += bool(notes)
        suffix = f"  [{', '.join(notes)}]" if notes else ""
        print(f"{prefix}{tag_id}  {file}:{lineno}{suffix}")
    for tag_id, section in sections.items():
        if wanted(tag_id) and tag_id not in seen:
            print(f"{prefix}{tag_id}  (no tag in tree; {section.task_file.name})")
    if problems:
        sys.exit(1)


def cmd_show(args: argparse.Namespace) -> None:
    repo = current_repo()
    prefix = core.tag_prefix()
    tag_id = args.id.removeprefix(prefix)
    for t_id, file, lineno, _ in core.find_tags(repo.root, prefix):
        if t_id == tag_id:
            print(f"at {file}:{lineno}")
    section = core.read_sections(repo, prefix).get(tag_id)
    if section is None:
        die(f"no task file documents {prefix}{tag_id}")
    print(f"from {section.task_file}\n")
    print(section.text, end="")


def cmd_list(_args: argparse.Namespace) -> None:
    repo = current_repo()
    prefix = core.tag_prefix()
    sections = core.read_sections(repo, prefix)
    in_tree = {t[0] for t in core.find_tags(repo.root, prefix)}
    for task_file in repo.task_files():
        ids = [i for i, s in sections.items() if s.task_file == task_file]
        placed = sum(i in in_tree for i in ids)
        title = core.task_title(task_file)
        print(f"{task_file.stem}: {title}\n    {len(ids)} planned, {placed} tagged in tree")


def cmd_strip(args: argparse.Namespace) -> None:
    repo = current_repo()
    prefix = core.tag_prefix()
    match args:
        case argparse.Namespace(all=True):
            ids_re = core.ID_PATTERN
        case argparse.Namespace(task=str(task)):
            ids_re = rf"{re.escape(task)}-[0-9]+"
        case argparse.Namespace(ids=[_, *_] as ids):
            ids_re = "(?:" + "|".join(re.escape(i.removeprefix(prefix)) for i in ids) + ")"
        case _:
            die("name tag IDs, or pass --task or --all")
    line_re = core.tag_line_re(prefix, ids_re)
    removed = 0
    for rel in sorted({t[1] for t in core.find_tags(repo.root, prefix)}):
        path = repo.root / rel
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not line_re.match(line)]
        if dropped := len(lines) - len(kept):
            removed += dropped
            if not args.dry_run:
                path.write_text("".join(kept))
            print(f"{rel}: {dropped} tag line(s)")
    print(f"{'would remove' if args.dry_run else 'removed'} {removed} tag line(s)")


def hook_command(event: str) -> str:
    """What git runs for `event`: the devtask on PATH, else this interpreter."""
    match shutil.which("devtask"):
        case str(exe):
            base = shlex.quote(exe)
        case None:
            base = f"{shlex.quote(sys.executable)} -m devtask"
    return f"{base} git-hook {event}"


def git_hooks_installed() -> bool:
    """True when every global hook is configured with the current command."""
    return all(
        core.git("config", "--global", "--get", f"hook.devtask-{event}.event") == event
        and core.git("config", "--global", "--get", f"hook.devtask-{event}.command")
        == hook_command(event)
        for event in HOOK_EVENTS
    )


def install_git_hooks() -> None:
    for event in HOOK_EVENTS:
        name = f"devtask-{event}"
        core.git("config", "--global", f"hook.{name}.event", event)
        core.git("config", "--global", f"hook.{name}.command", hook_command(event))
        print(f"installed global {event} hook: {name}")


def cmd_install_git_hook(_args: argparse.Namespace) -> None:
    install_git_hooks()


def cmd_uninstall_git_hook(_args: argparse.Namespace) -> None:
    for event in HOOK_EVENTS:
        core.git("config", "--global", "--remove-section", f"hook.devtask-{event}")
        print(f"removed global {event} hook: devtask-{event}")


def cmd_git_hook(args: argparse.Namespace) -> None:
    """Run by git: refuse a commit or push whose added lines contain a tag."""
    prefix = core.tag_prefix()
    diff = ("--no-color", "--no-ext-diff", "-U0")
    hits = []
    match args.event:
        case "pre-commit":
            hits = core.added_tag_lines(core.git("diff", "--cached", *diff) or "", prefix)
        case "pre-push":
            for ref_line in sys.stdin:
                match ref_line.split():
                    case [_, local_sha, _, _] if local_sha.strip("0"):
                        patch = core.git(
                            "log", "-p", *diff, "--format=", local_sha, "--not", "--remotes"
                        )
                        hits += core.added_tag_lines(patch or "", prefix)
    if hits:
        print(
            f"devtask: {args.event} blocked: added lines contain '{prefix}' tags:", file=sys.stderr
        )
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        print("Remove them first (devtask strip --all), then retry.", file=sys.stderr)
        sys.exit(1)


def cmd_complete(args: argparse.Namespace) -> None:
    """Print `value<TAB>description` lines for the fish completions."""
    root = core.repo_root(Path.cwd())
    if root is None:
        return
    repo = core.Repo.at(root)
    prefix = core.tag_prefix()
    match args.kind:
        case "tasks":
            for task_file in repo.task_files():
                print(f"{task_file.stem}\t{core.task_title(task_file)}")
        case "ids":
            sections = core.read_sections(repo, prefix)
            for tag_id in sorted(known_ids(repo, prefix)):
                section = sections.get(tag_id)
                print(f"{tag_id}\t{section.title if section else 'unmapped'}")


def fish_quote(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def fish_script(parser: argparse.ArgumentParser) -> str:
    """Fish completions generated from the parser, so they never drift."""
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    lines = [
        "# devtask completions, generated by `devtask completions fish`.",
        "complete -c devtask -f",
    ]
    for choice in sub._choices_actions:
        lines.append(
            f"complete -c devtask -n __fish_use_subcommand -a {choice.dest} "
            f"-d {fish_quote(choice.help or '')}"
        )
    for choice in sub._choices_actions:
        when = fish_quote(f"__fish_seen_subcommand_from {choice.dest}")
        for action in sub.choices[choice.dest]._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            match COMPLETE.get(action), action.choices:
                case str(kind), _:
                    values = f" -a {fish_quote(f'(devtask _complete {kind})')}"
                case None, [*choices]:
                    values = f" -a {fish_quote(' '.join(choices))}"
                case _:
                    values = ""
            # Dynamic values carry their own descriptions (value<TAB>title).
            dynamic = action in COMPLETE
            desc = f" -d {fish_quote(action.help)}" if action.help and not dynamic else ""
            longs = [o[2:] for o in action.option_strings if o.startswith("--")]
            match action.option_strings, longs:
                case [], _ if values:
                    lines.append(f"complete -c devtask -n {when}{values}{desc}")
                case [_, *_], [long, *_]:
                    takes_value = " -x" if action.nargs != 0 else ""
                    lines.append(
                        f"complete -c devtask -n {when} -l {long}{takes_value}{values}{desc}"
                    )
    return "\n".join(lines) + "\n"


def fish_completion_file() -> Path:
    config = core.xdg_base("XDG_CONFIG_HOME", Path.home() / ".config")
    return config / "fish" / "completions" / "devtask.fish"


def completions_current() -> bool:
    """True when the installed fish completions match this version's."""
    target = fish_completion_file()
    return target.is_file() and target.read_text() == fish_script(build_parser())


def install_completions() -> None:
    target = fish_completion_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fish_script(build_parser()))
    print(f"wrote {target}")


def cmd_completions(args: argparse.Namespace) -> None:
    if args.install:
        install_completions()
    else:
        print(fish_script(build_parser()), end="")


def ensure_setup() -> bool:
    """Install the git hooks, and fish completions if fish is present, when
    either is missing or stale. Returns whether anything was installed."""
    changed = False
    if not git_hooks_installed():
        install_git_hooks()
        changed = True
    if shutil.which("fish") and not completions_current():
        install_completions()
        changed = True
    return changed


def cmd_setup(_args: argparse.Namespace) -> None:
    if not ensure_setup():
        print("git hooks and completions are already up to date")


@functools.cache
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtask", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    def command(name: str, help_text: str | None = None) -> argparse.ArgumentParser:
        kwargs = {"help": help_text, "description": help_text} if help_text else {}
        return sub.add_parser(name, **kwargs)

    command("init", "set up this repo and turn the guard on")
    command("info", "show config, directories and guard state")
    p = command("path", "print this repo's task directory")
    p.add_argument("--state", action="store_true", help="print the state directory instead")
    p = command("guard", "show or set the Claude Code edit guard")
    p.add_argument("state", nargs="?", choices=("on", "off"), help="new guard state")
    p = command("new", "create a task file and print its path")
    COMPLETE[p.add_argument("task", help="task name, e.g. pr1234")] = "tasks"
    p.add_argument("--title", help="one-line title for the task")
    p = command("next-id", "print the next free tag for a task")
    COMPLETE[p.add_argument("task", help="task name")] = "tasks"
    p = command("scan", "list tags in the tree against the task files")
    COMPLETE[p.add_argument("--task", help="only this task")] = "tasks"
    p = command("show", "print where a tag is and what it asks for")
    COMPLETE[p.add_argument("id", help="tag ID, with or without the prefix")] = "ids"
    command("list", "list tasks and their progress")
    p = command("strip", "remove tag lines from the working tree")
    COMPLETE[p.add_argument("ids", nargs="*", help="tag IDs to remove")] = "ids"
    COMPLETE[p.add_argument("--task", help="remove every tag of this task")] = "tasks"
    p.add_argument("--all", action="store_true", help="remove every tag")
    p.add_argument("--dry-run", action="store_true", help="only report what would go")
    command("setup", "install the git hooks and fish completions if needed")
    command("install-git-hook", "add the global pre-commit/pre-push check")
    command("uninstall-git-hook", "remove the global pre-commit/pre-push check")
    p = command("completions", "print fish completions")
    p.add_argument("shell", nargs="?", choices=("fish",), default="fish", help="target shell")
    p.add_argument("--install", action="store_true", help="write them to fish's completions dir")

    # Hidden: called by git and by the fish completions.
    p = command("git-hook")
    p.add_argument("event", choices=HOOK_EVENTS)
    p.add_argument("hook_args", nargs="*")
    p = command("_complete")
    p.add_argument("kind", choices=("ids", "tasks"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd is None:
        parser.print_help()
        return
    globals()[f"cmd_{args.cmd.strip('_').replace('-', '_')}"](args)

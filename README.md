# devtask

For when you want an agent to plan and prepare a change but would rather
write the code yourself, whether to learn a codebase, keep your hand in, or
because the project doesn't accept LLM-written code.

Claude explores the codebase and plans the change. It may touch the
repository only to add **tag comments** at the places that need work:

```rust
fn build(self) -> Client {
    // #DEVTASK_pr1234-2
    let pool = Pool::new(self.max_conns);
```

Each tag points to a section of a **task file** kept outside the repository.
The section says what to change there, why, which questions to think through,
and how to tell when it's done. You work through the tags and write the code.
Then `/devtask:review` checks your implementation and answers with findings
and hints, not patches.

## Pieces

| Piece | What it does |
|---|---|
| `devtask` (`src/devtask/cli.py`) | CLI that manages task files, finds tags, maps them to their sections, and strips them. It also runs as the global `pre-commit` and `pre-push` git hook, which refuses any added line containing a tag |
| `hooks/guard.py` | Claude Code hook (logic in `src/devtask/guard.py`). In a guarded repo it denies any edit that changes more than tag-only lines |
| `skills/plan` | `/devtask:plan <issue/PR>` explores the code, places tags, and writes the task file |
| `skills/ask` | `/devtask:ask [tag] [question]` talks through one tag with explanations and hints, and can add notes to its section |
| `skills/review` | `/devtask:review [task]` reviews your implementation against the plan |

devtask writes nothing to the repository, including `.git/`. Its files
follow the [XDG Base Directory spec][xdg] and honor `XDG_CONFIG_HOME`,
`XDG_DATA_HOME` and `XDG_STATE_HOME`:

| What | Where (default) |
|---|---|
| Settings | `~/.config/devtask/config.toml` |
| Task files | `~/.local/share/devtask/repos/<repo>-<hash>/tasks/` |
| Guard flag, repo info | `~/.local/state/devtask/repos/<repo>-<hash>/` |

All worktrees of a repository share one set of these directories. Run
`devtask info` to see the paths in use.

[xdg]: https://specifications.freedesktop.org/basedir-spec/latest/

## Install

devtask is a [uv][uv] project and needs Python 3.11 or newer.

```sh
uv tool install ~/Workspace/devtask   # or `uv tool install -e .` while hacking on it
devtask setup                         # add the global git hooks and fish completions
```

`devtask setup` is optional: `devtask init` runs the same check first and
installs whatever is missing or out of date. Fish completions are only
written when `fish` is on `PATH`.

Then, in Claude Code:

```
/plugin marketplace add ~/Workspace/devtask
/plugin install devtask@devtask
```

The git hooks are [config-based hooks][hooks] (`hook.<name>.event` and
`hook.<name>.command` in your global config), which need a git version with config-based hook support (tested with git 2.55).
They run alongside each repository's own hooks, including Husky-style
`core.hooksPath` setups, instead of replacing them. The hook command is the
`devtask` found on `PATH` when they were installed. To remove them, run
`devtask uninstall-git-hook`.

The Claude Code hook runs `hooks/guard.py` from the plugin's own copy of
the repository with the system `python3` (3.11 or newer), so the plugin
works without the CLI installed.

[uv]: https://docs.astral.sh/uv/
[hooks]: https://git-scm.com/docs/githooks

## Use

```sh
cd some-project
devtask                         # no arguments: list the commands
devtask info                    # config, directories and guard state
devtask init                    # set up this repo and turn the guard on
claude                          # then: /devtask:plan 1234
devtask list                    # tasks and how many tags remain
devtask scan                    # each tag, where it is, and whether it is mapped
devtask show                    # every tag in the tree, where it is and what it asks for
devtask show --task pr1234      # the same for one task (or `show pr1234-2` for one tag)
devtask strip pr1234-2          # remove one tag (or --task pr1234, or --all)
# ...write the code...          # stuck? /devtask:ask pr1234-2 <question>
                                # then: /devtask:review pr1234
```

Fish completions cover every command and option. Task names and tag IDs
are completed with their titles, so `devtask show <Tab>` lists what each
tag is for. After an update, `devtask setup` or the next `devtask init`
regenerates them. `devtask completions fish` prints them instead.

The guard applies only in repositories where `devtask init` (or
`devtask guard on`) has run. Everywhere else the plugin does nothing.

## Tags

A tag is the prefix (default `#DEVTASK_`) followed by an ID such as
`pr1234-2`. A tag line holds nothing but the tag inside the file's own
comment syntax (`//`, `#`, `--`, `<!-- -->`, and so on), so the project
still builds while tags are present. To change the prefix:

```toml
# ~/.config/devtask/config.toml
tag = "@devtask:"
```

The git hook flags a tag anywhere in an added line. The Claude guard only
accepts tag-only lines, so Claude can't hide code next to a tag.

## Limits

- The Claude guard handles Edit/Write exactly. Its Bash check is a
  heuristic: it blocks `sed -i`, redirects into the repo, file-moving
  commands, formatters, and git commands that commit or rewrite. A
  determined script (for example `python -c` writing a file) gets past it.
  The git hook is the backstop for tags, and the skills tell Claude not to
  write code. Neither stops an LLM-written line that you commit yourself.
- `git commit --no-verify` and `git push --no-verify` skip the git hooks.
- Task files describe changes in prose and must not contain code meant to
  be pasted in. Quoting existing code as an example is allowed. These rules
  are in the skill instructions, and nothing enforces them.

## Develop and release

```sh
uv sync                         # dev tools: ruff, ty, git-cliff
uv run ruff check && uv run ruff format --check && uv run ty check src hooks scripts
uv run scripts/next_version.py  # what the next release would be, and why
```

CI (`.github/workflows/ci.yml`) runs these checks on Python 3.11 and 3.14,
smoke-tests the CLI, the git hook and the Claude guard, and shows a preview
of the next release in the run summary.

Write commit messages as [conventional commits][cc]. The type decides the
release:

| Commit | Release |
|---|---|
| `feat!: ...` or a `BREAKING CHANGE:` footer | major |
| `feat: ...` | minor |
| `fix:`, `docs:`, `refactor:`, `perf:` or anything else | patch |
| `ci:`, `test:`, `style:`, `build:`, `chore:` | none on their own |

`scripts/next_version.py` also compares the command-line interface at the
last tag with HEAD. Removing a command, option or choice, or making an
argument required, counts as major even if no commit says so. Adding one
counts as minor. The release uses whichever is larger. Before 1.0.0, a
major change bumps the minor version.

The CLI check can't see other breaking changes, such as a renamed config
key, a new tag format or moved data directories. Mark those with `!` or a
`BREAKING CHANGE:` footer.

To release, run the **Release** workflow from the Actions tab, or
`gh workflow run release.yml` (add `-f bump=minor` to force a level). It
runs CI, sets the version in `pyproject.toml`, `uv.lock` and
`.claude-plugin/plugin.json`, writes `CHANGELOG.md`, then commits, tags
and pushes to `main` and publishes a GitHub release with the built
package. The workflow pushes to `main` with the workflow token, so branch
protection on `main` must let GitHub Actions push.

[cc]: https://www.conventionalcommits.org

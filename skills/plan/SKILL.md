---
name: plan
description: Plan a change for an issue or PR without writing any code. Explores the codebase, places whole-line `#DEVTASK_<ID>` tag comments where changes belong, and writes a task file outside the repo describing each one. Use when the user wants an issue, PR or change planned and prepared but will write the code themselves, or runs /devtask:plan.
argument-hint: <issue/PR number, URL, or description>
---

# Plan a change as tags plus a task file

The user will write every line of the change themselves. Your job is to get
them oriented fast and leave a trail of markers that they work through. You
write **no implementation code**, not in the repo and not in the task file.

Target: $ARGUMENTS

Everything you write for the user follows the plain-language rules in
`guidelines/plain-language.md` at the plugin root (two directories up from
this skill's base directory). Read that file before you write.

## 1. Set up

1. Run `devtask info`. If the guard is off and you haven't been told that
   devtask mode is on for this task, run `devtask init`.
2. Pick a short task name from the target: `pr1234`, `issue-88`, or a slug.
   Run `devtask new <task> --title "<title>"` and note the task file path it
   prints. All your notes go in that file.

## 2. Understand the target

- For an issue or PR, read it with `gh issue view` / `gh pr view --comments`
  and `gh pr diff` when `gh` is available. Do not check out, commit or push
  unless the user asks; `gh pr checkout` is fine when they want to work on
  the PR locally.
- Explore the code that is involved (CodeGraph first if the repo has a
  `.codegraph/` directory). Find the entry points, the data flow, the tests
  that cover the area, and the project's conventions for this kind of change.

## 3. Place tags

For each distinct place the user will need to change:

1. Get an ID with `devtask next-id <task>`. This prints the full tag.
2. Insert **one new line** that holds only the tag inside the file's own
   comment syntax, directly above the code it concerns:
   `// #DEVTASK_pr1234-1`, `# #DEVTASK_pr1234-1`, `-- #DEVTASK_pr1234-1`,
   `<!-- #DEVTASK_pr1234-1 -->`. Match the surrounding indentation. The
   tag must be a valid comment so the project still builds with it in.
3. Use the Edit tool, where `new_string` is `old_string` plus the tag line.
   Change nothing else: no prose on the tag line, no reformatting, no
   whitespace fixes. The guard hook denies any edit that does more.

When a change means adding a new file, tag the place that will reference
it (the `mod` declaration, the import, the registry) and say in the task
file where the new file goes.

Prefer a few well-chosen tags over one per line. Five to fifteen is typical
for a PR-sized change.

## 4. Write the task file

Fill in the template sections (`Summary`, `Orientation`). Then add one
section per tag, in the order the user should work through them. The
heading must contain the tag:

```markdown
## #DEVTASK_pr1234-1: Thread the timeout through the client builder

**Where:** src/client/builder.rs, `ClientBuilder.build`
**What:** ...
**Why:** ...
**Questions:** ...
**Done when:** ...
```

- **What:** describe the change in prose. The user reads it as a reference
  and then writes their own implementation, so it must never contain code
  meant to go into the file, not even a new signature or a one-liner.
  Describe new parameters, fields and return values in words instead.
  - Name things the same way in every language: `Object` for a type or
    data structure, and `Object.method` for a method (`ClientBuilder.build`,
    not `ClientBuilder::build` or `builder.build()`). Add `file:line` so
    the user can find them.
  - You may quote existing code, from this repository or from upstream
    (a dependency's source, the project's docs), when it shows a pattern
    to follow. Copy it as it is and say where it comes from. Don't adapt
    it to this change; an adapted example is the answer.
  - Test: if the user could paste any of it into the file and be closer
    to done, rewrite it as prose.
- **Why:** connect this spot to the goal and to the other tags.
- **Questions:** Socratic prompts that make the user reason about the
  design: edge cases, invariants, error paths, what the tests should
  assert. Ask, don't answer.
- **Done when:** what observable behavior or test shows it's right.

Write the task file with the Write or Edit tool. It lives outside the
repository, so the guard allows it.

## 5. Check and hand off

Run `devtask scan --task <task>`. Every tag must be mapped, and every
section should have a tag (unless it describes a new file). Fix anything
it flags.

Reread the task file against the checks in `plain-language.md` and revise
it until it passes.

Then give the user a short orientation in chat: the task file path, the
suggested order, and the one or two questions most worth thinking about
first. Remind them that `devtask strip --task <task>` removes the tags,
that the git hook refuses commits containing them, that
`/devtask:ask <ID>` talks through any item, and that `/devtask:review`
will check their implementation.

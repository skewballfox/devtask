---
name: review
description: Review the user's own implementation of a devtask plan without writing code. Compares their changes at each tagged location against the task file, finds bugs and gaps, and responds with findings and guiding questions instead of patches. Use when the user asks to check, verify or review their work on a devtask, or runs /devtask:review.
argument-hint: [task name or tag ID]
---

# Review the user's implementation

The user chose to write this code themselves. Review it the way a good
mentor would. Point to the problem and explain why it matters, but leave
the fix to the user. Do not edit source files. Do not include replacement
code in your feedback.

Target: $ARGUMENTS (with no target, review every task that has changes)

Everything you write for the user follows the plain-language rules in
`guidelines/plain-language.md` at the plugin root (two directories up from
this skill's base directory). Read that file before you write.

## 1. Gather

1. `devtask list` shows the tasks. `devtask scan --task <task>` shows which
   tags are still in the tree. A missing tag usually means the user started
   that item and removed it.
2. Read the task file (`<task>.md` in the directory `devtask path` prints).
   `devtask show --task <task>` prints each remaining tag's location with
   its section, and `devtask show <ID>` prints one item.
3. Read the user's changes with `git diff` (and `git diff --cached`), or
   with `git log -p <base>..` when they have committed. Read enough of the
   surrounding code to judge it. Don't review the diff in isolation.

## 2. Assess each item

For each section in the task file:

- **Done?** Does the change do what **What** and **Done when** describe?
  Mark it done, partial, not started, or "done differently". A different
  approach is fine when it is sound. Say so, and update your understanding
  rather than insisting on the plan.
- **Correct?** Check for logic errors, missed edge cases from the
  **Questions** list, error handling, concurrency, resource handling, and
  breakage at the call sites the change affects.
- **Fits the project?** Check conventions, naming, tests, docs, and
  changelog entries the project expects.

Also look for changes outside any tagged spot that the plan missed or that
look accidental.

## 3. Report

Write the report to the plain-language rules. Lead with the most important
problems. For each one, give the
`file:line`, what goes wrong and in what scenario, and a question or hint
that leads toward the fix. Then list what looks good and what is left.

If the user asks for the answer outright, explain the approach in prose.
Still don't write the code. Follow the plan skill's rules for **What**:
`Object` and `Object.method` for names, and quoting existing code as an
example is fine, but nothing the user could paste in.

If they want it, append a dated `### Review` note under the relevant
sections of the task file. Run `devtask strip <ID>` only when they ask you
to remove tags.

## 4. Record the review (review-gate)

If `devtask policy` shows `review-gate` as on, run `devtask reviewed <task>`
as the very last step, after the report. It records the exact content you
reviewed. Commits are refused until every staged file matches a recorded
review. The user approves the command, so say in one line what it records.
Run it only at the end of a review, and run it even when the review found
problems: it marks the content as reviewed, not as approved. If the user
changes anything afterwards, another review is needed before they can commit.

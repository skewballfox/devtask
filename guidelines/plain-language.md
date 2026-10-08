# Plain language (ISO 24495-1)

Everything devtask has you write for the user — task files, the hand-off
orientation, review findings, answers in chat — follows ISO 24495-1:2023,
*Plain language — Part 1: Governing principles and guidelines*. The standard
calls a text plain when its readers can easily find what they need,
understand it, and use it. It rests on four principles. Below, each one is
turned into checks for this kind of writing.

The reader is a developer who is about to write the change themselves. They
know how to program, they may not know this codebase, and they will read
your text while working in their editor.

## 1. Relevant: readers get what they need

- Write for that reader and their next action. Before each section, ask
  what they need to know to act, and leave out the rest.
- Include what they can't easily find out on their own: where to look, why
  this spot matters, what can go wrong. Leave out what any developer
  already knows and history that doesn't affect the change.
- Match the document to its job. A task-file section helps someone make one
  change. A review finding helps someone fix one problem. Don't turn either
  into an essay.

## 2. Findable: readers can easily find what they need

- Put the most important point first: in the document, in each section and
  in each paragraph. In a review, lead with the most serious problem.
- Use headings that say what the section is about ("Thread the timeout
  through the builder", not "Part 2").
- Keep the fixed labels (**Where**, **What**, **Why**, **Questions**,
  **Done when**) in the same order every time so the reader can scan.
- Use lists for separate items or steps, and keep a list's items
  grammatically parallel. Use a table only when readers compare items
  across the same attributes.
- Give exact locations (`file:line`, function and type names) so the reader
  can jump straight there.
- Keep background separate from what the reader has to act on. Put it after
  the essentials or link to it.

## 3. Understandable: readers can easily understand what they find

- Prefer familiar words. Use the codebase's own names for things. Define a
  term or abbreviation the first time it appears if the reader may not
  know it.
- Use one term for one thing. Don't switch between "handler", "callback"
  and "hook" for the same object.
- Write short sentences with one main idea each, ideally under about 25
  words. Put the subject and verb near the start.
- Use the active voice and say who acts: "`build` drops the timeout", not
  "the timeout is dropped".
- Address the reader as "you" and give instructions as direct imperatives
  ("Check the error path").
- State things positively when you can ("keep X", rather than "don't fail to
  keep X").
- Avoid hedging and filler ("basically", "it's worth noting that", "simply").
  If you are unsure, say what you are unsure about and why.
- Keep paragraphs short and about one topic. Link them so the reasoning
  reads as one line of thought.

## 4. Usable: readers can easily use the information

- Make every instruction one the reader can act on: name the place, the
  change in prose, and how to tell it worked.
- Say what to do, not just what is wrong: each problem comes with the
  scenario that triggers it and a question or hint toward the fix.
- Keep steps in the order the reader will do them.
- Before you hand off, reread as the reader would. Check that they could
  find each point, understand it on first read, and act on it without
  asking you. Fix anything that fails.
- When the user says something was unclear, revise that part. Don't just
  explain it again in chat.

These rules shape how you write. They don't change what devtask allows you
to write: still no ready-to-paste implementation code.

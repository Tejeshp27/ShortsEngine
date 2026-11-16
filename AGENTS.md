# AGENTS.md — instructions for Codex

**Read `CLAUDE.md` first and follow it exactly.** It holds every rule for this
project: what it is, how to write code, cost limits, safety rules, the roadmap and
the current phase. This file is deliberately short so the two never disagree.

## Who does what

- **Claude Code is the main assistant** on ShortsEngine. Codex is used now and then
  for smaller tasks.
- Do not copy `CLAUDE.md` into this file. If a rule needs to change, change it in
  `CLAUDE.md`.

## Before you finish a task

1. Follow the "How to write code" rules in `CLAUDE.md`: beginner-friendly code, a
   header comment on every file, and a full plain-English docstring (inputs, what it
   returns, an example where useful) on every function.
2. Add or update tests, and run `pytest`.
3. Update `README.md` and the "Current phase" section of `CLAUDE.md` if behaviour or
   architecture changed.
4. In your summary, list the files you changed so Claude Code can review them in its
   next session.

---
name: doc-check
description: Documentation check before opening a PR. Reviews the branch's changes and updates CLAUDE.md, the README status table, docs/PHASES.md or skills only where that makes them more useful. Run before `gh pr create`; the pre-PR hook blocks until it has run.
user_invocable: true
---

# Documentation check

## Procedure

1. **What changed on the branch**

   ```bash
   BASE=$(git merge-base HEAD origin/main)
   git diff --name-only "$BASE"...HEAD
   git diff "$BASE"...HEAD
   ```

2. **Read the docs those changes touch:** `CLAUDE.md`, `README.md` (status
   table and roadmap), `docs/PHASES.md`, and any skill under `.claude/skills/`
   for the area changed.

3. **Update only where it helps.**

   Update when:
   - Something was **verified on the Pi or in the car**: move the README row to
     ✅ and say what was run and in what car state. Also update the Status
     section in `CLAUDE.md`.
   - A hardware fact was learned (a PID this car does or doesn't support, a
     threshold this engine needs, an adapter quirk). These go in `CLAUDE.md`'s
     constraints table.
   - A phase task finished or changed scope: tick it in `docs/PHASES.md`.
   - A workflow changed (deploy path, boot setup, new command): update the
     skill that teaches it (`deploy-to-pi`, `car-ai-run`, `add-sensor`,
     `car-ai-feature`).
   - A new invariant or "looks odd on purpose" rule appeared.

   Don't update for:
   - Bug fixes that don't change a pattern or fact.
   - Adding a sensor with `add-sensor` (it's self-documenting in `sensors.py`).
   - Styling-only changes.
   - Anything that would make the docs longer without making them more useful.

   **Never** promote a status row to ✅ because code was written. If it wasn't
   run on hardware, it stays ⚠️.

4. **Finish one of two ways**

   - Docs updated: stage them (`git add CLAUDE.md README.md docs/ .claude/`)
     and summarise what changed and why.
   - Nothing needed: say why in one line, then write the marker so the pre-PR
     hook knows the check ran:

     ```bash
     git diff --name-only "$BASE"...HEAD > .doc-check-passed
     git add .doc-check-passed
     ```

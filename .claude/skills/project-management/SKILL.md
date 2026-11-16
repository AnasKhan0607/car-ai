---
name: project-management
description: GitHub workflow for Car AI — phases, issues, issue-N branches, PRs into the fork's main, and keeping the README status table honest. Use when planning or starting work, creating issues, picking the next task, committing, or opening a PR.
---

# Car AI project management

"No task, no work": every change has an issue, a branch, and a PR.

## Where things live

- **Home repo: `AnasKhan0607/car-ai`** (a fork). Issues, labels, branches and
  PRs all go here. PR base is the fork's `main`.
- `iqureshi123/car-ai` is the upstream this was forked from. Read-only for this
  account. Never target it with `gh` unless the owner asks for an upstream PR.
  `gh` in a fork can default to the parent, so **always pass
  `--repo AnasKhan0607/car-ai`**.
- The plan is `docs/PHASES.md`. Each phase's tasks are issues with a
  `phase-N` label.

## Workflow

```bash
# 1. pick or create the issue
gh issue list --repo AnasKhan0607/car-ai --label phase-1
gh issue create --repo AnasKhan0607/car-ai --title "..." --body "..." \
  --label phase-1 --label enhancement

# 2. branch from an up-to-date main
git fetch origin && git checkout -b issue-12-coolant-thresholds origin/main

# 3. commit as you go (conventional commits, reference the issue)
git commit -m "fix: raise coolant thresholds for the B46 (#12)"

# 4. verify (see below), run /doc-check, then
git push -u origin HEAD
gh pr create --repo AnasKhan0607/car-ai --base main --body "...Closes #12"
```

## Verification decides "done", not the code

| Change touches | Minimum before the PR |
|---|---|
| tools / alerts / `sensors.py` | `python app.py --selftest` |
| UI | `--mock` with a scenario that exercises the touched state (`car-ai-run`) |
| anything that talks to the car | run live on the Pi (`deploy-to-pi`) |

A README status row moves to ✅ **only** in the PR that ran it on real hardware,
and the PR body says what was run, where, and in what car state (parked,
idling, driving). "Written but not run" is ⚠️, always.

## Labels

| Label | Use |
|---|---|
| `phase-0` … `phase-7` | Which phase in `docs/PHASES.md` |
| `bug` / `enhancement` / `documentation` | Kind of change |
| `hardware` | Needs the Pi or the car to finish or verify |
| `blocked` | Waiting on hardware, a purchase, or an owner decision |

## PR body template

```markdown
## Summary
- ...

## Verified
- [ ] `python app.py --selftest`
- [ ] `--mock --scenario ...` in a browser
- [ ] Live on the Pi: <what was run, car parked / idling / driving>

## Status table
<rows changed, or "no change">

Closes #N
```

## Never

- Commit to `main` directly or push to upstream.
- Promote a status row without running it on hardware.
- Commit drive logs (`car_ai_logs/`): they are telemetry from a real car.
- Buy or assume hardware not in `docs/PHASES.md` without asking the owner.

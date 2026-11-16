#!/usr/bin/env bash
# Pre-PR documentation check (PreToolUse hook on Bash).
# Blocks `gh pr create` when the branch changes code without touching any
# docs, so the README status table and CLAUDE.md can't silently drift.
# Exit codes: 0 = allow, 2 = block with message.
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

echo "$COMMAND" | grep -q "gh pr create" || exit 0

BASE=""
for ref in origin/main main; do
  git rev-parse --verify -q "$ref" >/dev/null && { BASE="$ref"; break; }
done
[ -z "$BASE" ] && exit 0
MERGE_BASE=$(git merge-base HEAD "$BASE" 2>/dev/null || echo "")
[ -z "$MERGE_BASE" ] && exit 0

CHANGED=$(git diff --name-only "$MERGE_BASE"...HEAD 2>/dev/null || echo "")
STAGED=$(git diff --cached --name-only 2>/dev/null || echo "")
ALL=$(printf '%s\n%s\n' "$CHANGED" "$STAGED")

# Code whose behaviour the docs describe. Styling-only changes are exempt.
echo "$ALL" | grep -qE '^(app\.py|car_ai/.*\.(py|js|html)|scripts/|requirements\.txt)$' || exit 0

# Any doc touch (or the marker /doc-check writes) unblocks.
echo "$ALL" | grep -qE '^(CLAUDE\.md|README\.md|docs/|\.claude/skills/|\.claude/agents/|\.doc-check-passed$)' && exit 0

echo "BLOCKED: this branch changes code but no docs."
echo "Run /doc-check: it updates CLAUDE.md / README status / docs/PHASES.md if needed,"
echo "or writes .doc-check-passed if nothing needs changing."
exit 2

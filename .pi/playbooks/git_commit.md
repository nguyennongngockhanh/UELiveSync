# Playbook: Git Commit Safety

Never commit unless explicitly requested.

Before commit, always show:
git status --short --untracked-files=all
git diff --stat
git diff --name-only

If tests/ files are ignored, use git add -f only when explicitly requested.

Do not include:
- temporary screenshots
- stale runtime evidence
- large diagnostic patches unless user explicitly approves
- accidentally deleted old evidence files

Before commit, verify no accidental deletion:
git status --short --untracked-files=all | grep '^ D' || true

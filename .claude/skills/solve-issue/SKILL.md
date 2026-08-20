---
name: solve-issue
description: Use when the user asks to fetch, pick up, or solve a specific GitHub issue in this repo by number or URL — e.g. "solve issue #52", "fix issue 61", "pick up https://github.com/.../issues/52". Requires an explicit issue reference; do not use for vague "what should I work on next" requests.
---

# Solve Issue

## Overview
Fetches a specific GitHub issue in this repo, implements it end to end via the matching
`ecc:orch-*` orchestration skill, and closes the loop on GitHub: the PR auto-closes the issue and
the skill leaves an explicit comment on the issue itself linking to the PR.

## Workflow

1. **Identify the issue** — require an explicit issue number or URL from the user's request. If
   none was given, ask for one; do not auto-pick from `gh issue list`.

2. **Fetch it**:
   ```bash
   gh issue view {n} --json number,title,body,labels,state,url
   ```
   If `state` is `CLOSED`, confirm with the user before proceeding.

3. **Classify the change shape** from labels first, body text second. This repo mixes GitHub's old
   default labels (`bug`, `enhancement`, `documentation`) with newer `type:*`/`area:*`/
   `difficulty:*` labels — check both (`gh label list`):

   | Signal | Skill / handling |
   |---|---|
   | `bug` label, or body describes broken/wrong/crashing behavior | `ecc:orch-fix-defect` |
   | `type:feature` / `enhancement`, body describes a capability that doesn't exist yet (default) | `ecc:orch-add-feature` |
   | `type:feature` / `enhancement`, body describes changing existing behavior | `ecc:orch-change-feature` |
   | `type:chore`, body describes restructuring existing code, no behavior change | `ecc:orch-refine-code` |
   | `type:chore`, trivial non-behavioral task (dep bump, license file, config tweak) | handle directly, no orch-* |
   | `type:docs` / `documentation` | handle directly (or `ecc:update-docs` for a codemap/doc sync), no orch-* |
   | `type:investigation` | handle directly as a spike/report, no orch-* |
   | No clear label/signal | ask the user which shape it is |

4. **Create a branch off up-to-date `main`**:
   ```bash
   git fetch origin main
   git checkout -b {prefix}/{n}-{slug} origin/main
   ```
   `{prefix}` from the classification: `fix` / `feat` / `refactor` / `chore` / `docs`. `{slug}` is a
   short kebab-case cut of the issue title.

5. **Do the work**:
   - Code-shaped issues → invoke the chosen `ecc:orch-*` skill via the `Skill` tool, passing the
     issue number, title, body, and acceptance criteria as its task description. That skill owns
     its own Gate 1 (plan approval) / Gate 2 (pre-commit confirmation) — don't add a redundant gate
     here.
   - Direct-handling issues (docs / trivial chore / investigation) → make the change (or write the
     spike's findings) directly, still following AGENTS.md §7 `commit-code` workflow (quality
     checks, staging, conventional commit message) since no orch-* skill is doing that for you.

6. **Push and open the PR**:
   ```bash
   git push -u origin {branch}
   gh pr create --draft --title "{type}: {short description}" --body "$(cat <<'EOF'
   ## Summary
   Closes #{n}.

   - {bullet(s) describing what changed and why}

   ## Test plan
   - [x] {checks actually run, e.g. `uv run poe check`, `uv run poe test`}
   EOF
   )"
   ```
   Always open as a **draft** PR (`--draft`) — this repo runs Jules' automated review on
   ready-for-review PRs, and Jules calls are costly, so draft status prevents an unwanted review
   run until the PR is deliberately marked ready.
   `Closes #{n}` is required (not a plain `#{n}` mention) — matches this repo's existing convention
   (e.g. PR #16, #20) and lets the issue auto-close on merge.

7. **Comment on the issue itself**, linking back to the new PR:
   ```bash
   gh issue comment {n} --body "Opened #{pr-number} to address this: {pr-url}"
   ```
   Required even though the PR already links to the issue — it leaves an explicit trail on the
   issue for anyone watching it who isn't watching the PR list.

## Common Mistakes
- Guessing at an issue instead of asking when none was given.
- Picking `orch-add-feature` by label alone when the body actually describes changing existing
  behavior (or vice versa) — read the body, don't just pattern-match the label.
- Running `orch-refine-code` on a `type:chore` issue that isn't actually a refactor (e.g. "add
  LICENSE") — wastes a TDD pipeline on a one-file addition.
- Using a plain `#{n}` mention instead of `Closes #{n}` in the PR body.
- Forgetting the separate `gh issue comment` — GitHub's auto-link isn't enough per this skill's
  contract.
- Pushing to `main` directly instead of a feature branch.

---
name: jules-feedback
description: Use when the user asks to process, triage, or act on Jules' (the automated PR-review bot) latest review comment on a pull request in this repo — e.g. "handle Jules' feedback", "triage the Jules comments", "go through Jules' latest review".
---

# Jules Feedback

## Overview
Jules posts an automated review comment (marked `<!-- jules-pr-reviewer -->`) on PRs in this repo.
This skill turns that comment into: fixed code in meaningful commits, technical pushback where
Jules is wrong, and tracked GitHub issues for anything out of scope for the current PR.

**REQUIRED SUB-SKILL:** superpowers:receiving-code-review — verify every finding against the
codebase before acting on it, don't implement on faith.

## Workflow

1. **Identify the PR** — default to the current branch's open PR (`gh pr view --json number,url`);
   ask if ambiguous.

2. **Fetch the newest Jules comment** (top-level or inline; take the latest by `created_at`):
   ```bash
   gh api repos/{owner}/{repo}/issues/{pr}/comments --jq '[.[] | select(.body | contains("jules-pr-reviewer"))] | last'
   gh api repos/{owner}/{repo}/pulls/{pr}/comments --jq '[.[] | select(.user.login | test("jules"; "i"))]'
   ```

3. **Evaluate every finding** via superpowers:receiving-code-review. Sort each into: in-scope real
   issue / out-of-scope real issue / false positive.

4. **Fix in-scope findings**, one logical fix per commit (AGENTS.md §7 commit-code workflow still
   applies). Pick the orchestration skill by finding shape:

   | Finding shape | Skill |
   |---|---|
   | Something is broken/wrong (default) | `ecc:orch-fix-defect` |
   | Pure structural cleanup, no behavior change | `ecc:orch-refine-code` |
   | Existing feature's behavior should change | `ecc:orch-change-feature` |
   | Genuinely missing capability | `ecc:orch-add-feature` |

5. **False positives**: implement nothing; reply with the technical reasoning (no "you're right!").

6. **Out-of-scope findings**: if it's bigger than this PR, needs its own design/testing, or is
   backlog-shaped, `gh issue create` matching this repo's existing shape — `## Problem /
   Motivation`, `## Current Behavior`, `## Proposed Approach`, `## Acceptance Criteria` (see
   `gh issue list` for examples) — with labels from `gh label list` (a `type:*`, an `area:*`, and a
   `difficulty:*` if scope is clear). Skip filing for anything trivial enough to just fix now.

7. **Reply on the PR**, one reply per finding-group: fixed / pushed back (with reasoning) / filed
   as issue #N. Use `gh pr comment` for top-level replies, or
   `gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies` for inline threads.

8. **Push** the commits.

## Common Mistakes
- Implementing a finding without checking it against the actual code — Jules can be wrong (e.g.
  flagging ordinary process docs as "prompt injection").
- One giant commit instead of one per logical fix.
- Filing an issue for something trivial, or silently fixing something out of scope instead of
  tracking it.
- Not replying on the PR — without a reply, nobody knows what happened to each finding.

# CI Pre-commit Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip `no-commit-to-branch` pre-commit hook in CI workflow.

**Architecture:** Add `SKIP: no-commit-to-branch` env variable to the pre-commit workflow step.

**Tech Stack:** GitHub Actions, pre-commit.

## Global Constraints
None.

---

### Task 1: Update CI Workflow

**Files:**
- Modify: `.github/workflows/ci.yml:21-22`

**Interfaces:**
- Consumes: None
- Produces: Updated CI workflow

- [ ] **Step 1: Modify the CI workflow file**
Update the "Run pre-commit" step in `.github/workflows/ci.yml` to include the `SKIP` environment variable:
```yaml
    - name: Run pre-commit
      env:
        SKIP: no-commit-to-branch
      run: uv run pre-commit run --all-files
```

- [ ] **Step 2: Commit the changes**
```bash
git add .github/workflows/ci.yml
git commit -m "ci: skip no-commit-to-branch hook in CI"
```

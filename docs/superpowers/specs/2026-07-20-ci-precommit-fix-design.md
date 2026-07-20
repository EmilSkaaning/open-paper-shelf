# Design Spec: CI Pre-commit Hook Fix

## Goal
Fix the CI workflow failure where the `no-commit-to-branch` pre-commit hook blocks the CI run on the `main` branch.

## Background
The `no-commit-to-branch` hook in `.pre-commit-config.yaml` is designed to prevent direct commits to protected branches (like `main`) on local developer setups. However, when the CI runs on pushes or pull requests, the checkout is often on a ref that triggers this check, leading to false positives and build failures.

## Proposed Design
We will set the `SKIP` environment variable for the `pre-commit` step in `.github/workflows/ci.yml` to ignore the `no-commit-to-branch` hook during CI execution.

### Trade-offs

1. **Option 1: Skip `no-commit-to-branch` via the `SKIP` environment variable in CI (Chosen)**
   - *Pros:* Keeps the local protection for developers while allowing CI builds to pass seamlessly. Minimal risk and change.
   - *Cons:* The CI does not verify that direct commits to `main` are disabled, but this is typically enforced by repository branch protection rules on the hosting service (GitHub) anyway.

2. **Option 2: Remove the hook from `.pre-commit-config.yaml` entirely**
   - *Pros:* Simpler configuration.
   - *Cons:* Removes local branch protection for developers, increasing the risk of accidental pushes to `main`.

3. **Option 3: Use the official pre-commit GitHub Action**
   - *Pros:* Handles skipping/CI behavior automatically.
   - *Cons:* Changes the current package management / caching approach setup via `uv` in the workflow.

## Proposed Changes

### `.github/workflows/ci.yml`
```yaml
    - name: Run pre-commit
      env:
        SKIP: no-commit-to-branch
      run: uv run pre-commit run --all-files
```

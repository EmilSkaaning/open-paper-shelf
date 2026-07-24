# Agent Developer Guidelines

This document governs how work is done and how function/class documentation and Git commits are carried out in the `open-paper-shelf` repository.

## 1. General Guidelines

### Python Virtual Environment
* Always execute python commands and tools using `uv` to ensure proper environment isolation.
* Example: `uv run pytest` or `uv run ruff format .`

### Testing
* Verify your changes by running unit tests before making any commit.
* Run tests using poe task runner or pytest:
  ```bash
  uv run poe test
  # or
  uv run pytest
  ```

### Coding & Documentation Standards (`func-documentation` skill)
* **Type Hints**: Add or update type hints for any new or modified functions/classes.
* **Docstrings**: Provide clean docstrings describing parameters, return values, and exceptions for any new or modified functions/classes. Use standard Python docstring style.
* **Exclusions**: Skip files where the changes are purely deletions or trivial (e.g. config, constants, `__init__.py`).

---

## 2. Core Workflow: commit-code

Use this workflow whenever committing changes or saving work.

### Step 1 — Understand the changes
The agent must run `git diff` and `git status` in parallel to understand what changed and why. The agent should read any relevant modified files if the diff alone is not enough to write a precise commit message.

### Step 1b — Decide whether to split into multiple commits
The agent must inspect the full diff across all changed files (not just one file at a time) and group changes by logical concern.
* The agent should split changes into multiple commits when the diff contains *two or more genuinely unrelated logical changes* (e.g. a bug fix in one module plus a new feature in another).
* The agent must *not* split:
  * A single feature/fix that happens to touch several files
  * Small incidental changes tightly coupled to the main change (e.g. a helper used by the new code)
If a split makes sense, the agent must inform the user of the proposed grouping and commit order before proceeding, and repeat Steps 2–6 (stage, message, commit) once per group. If everything belongs to one logical change, the agent should proceed as a single commit.

### Step 2 — Update documentation
For each changed Python file, the agent must apply the `func-documentation` standards described in the general guidelines. The agent must re-run `git diff` after this step to ensure documentation changes are staged together with the code changes in Step 4.

### Step 3 — Quality checks
The agent must run the following checks. If either fails, the agent must report the errors to the user and stop — the agent must not commit broken code.

*Ruff (format + lint):*
```bash
uv run ruff format --check .
uv run ruff check .
```

*Type checking (pyrefly):*
```bash
uv run pyrefly check
```

If checks fail, the agent should offer to auto-fix what can be fixed automatically:
* `uv run ruff format .` — fixes formatting
* `uv run ruff check --fix .` — fixes auto-fixable lint issues
* Pyrefly errors must be fixed manually.

The agent must re-run checks after any auto-fix before proceeding.

### Step 4 — Stage files
The agent must stage only the files relevant to the logical change, including any documentation files updated in Step 2. The agent should prefer explicit file paths over `git add .` to avoid accidentally including unrelated or sensitive files.
If untracked files exist that are unrelated to the change, the agent must leave them unstaged.

### Step 5 — Write the commit message
The agent must use [Conventional Commits](https://www.conventionalcommits.org/) format:
```
<type>(<scope>): <short description>

[optional body]
```
* **Types**: feat, fix, refactor, chore, docs, test, ci, perf
* **Scope**: the module, pipeline, or component affected
* **Short description**: imperative mood, lowercase, no trailing period

The agent must construct the message, then show it to the user for confirmation before committing.

### Step 6 — Commit
The agent must execute the commit using the agreed message:
```bash
git commit -m "$(cat <<'EOF'
type(scope): short description

Optional body here.
EOF
)"
```
The agent must not push. If splitting into multiple commits (Step 1b), the agent must repeat Steps 4–6 for each remaining group before finishing.

---

## 3. Commit Message Rules & Examples

### Hard Rules — Never Break These:

| Rule | Reason |
|------|--------|
| Never exceed 50 chars in subject | Git truncates it in logs and UIs |
| Never use generic messages | "update code", "fix bug", "changes", "wip", "misc" tell reviewers nothing |
| Never commit unrelated files together | One logical change per commit keeps history bisectable |
| Never skip quality checks | Broken commits block the team and CI |
| Never commit secrets or credentials | Check .env, config files before staging |

### Examples

* **Good**:
  * `feat(de_novo): enumerate all 15 alphafold model weights`
  * `fix(estrous): correct tissue mask path for wt samples`
  * `refactor(auth): replace session tokens with short-lived JWTs`
  * `chore(deps): bump modal to 0.67.0`

* **Bad**:
  * `update code` (generic, meaningless)
  * `fix bug` (which bug? in what?)
  * `WIP` (not a commit)
  * `changed some stuff` (tells reviewers nothing)
  * `feat: implemented the new de novo alphafold model weight enumeration system` (way over 50 chars)

# Open Paper Shelf - TODO

This file tracks the implementation steps to set up the foundational architecture for the Open Paper Shelf project.

## Task 1: Initialize Project Structure
- [x] Create `backend/` directory with `__init__.py` and a basic `main.py` (FastAPI).
- [x] Create `frontend/` directory with `__init__.py` and a basic `app.py` (Streamlit).
- [x] Create `tests/` directory with `__init__.py` and a basic `test_backend.py`.
- [x] Create a comprehensive `.gitignore` file for Python, Pyrefly, and Streamlit.

## Task 2: Configure Tooling
- [ ] Update `pyproject.toml` to include `streamlit` and `pyrefly` in the dependencies.
- [ ] Configure `tool.poe.tasks` in `pyproject.toml`:
  - `check`: Run `pyrefly` for static type checking.
  - `test`: Run `pytest` for unit testing.
  - `dev`: Run `uvicorn backend.main:app --reload` and `streamlit run frontend/app.py` concurrently.

## Task 3: Setup CI/CD
- [ ] Create `.github/workflows/ci.yml`.
- [ ] Configure the workflow to trigger on push and pull requests to the `main` branch.
- [ ] Add steps to checkout code, install dependencies (using `uv`), and run `poe check` and `poe test`.

## Task 4: Git Workflow Setup
- [ ] Commit all boilerplate code and configuration to a new branch (e.g., `setup/foundation`).
- [ ] Push the branch to GitHub and create a Pull Request.
- [ ] In GitHub repository settings, protect the `main` branch: require PRs, require status checks to pass before merging.

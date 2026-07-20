# Repository Foundation & Architecture Design

## Goal
Establish a robust, best-practices repository foundation for the Open Paper Shelf project. The project will feature a decoupled architecture with a FastAPI backend and a Streamlit frontend. It will incorporate automated tooling (Poe the Poet), strict type checking (Pyrefly), and GitHub Actions CI.

## Architecture
- **Backend**: FastAPI serving REST endpoints, using Pydantic for data validation. Located in `backend/`.
- **Frontend**: Streamlit application consuming the FastAPI endpoints. Located in `frontend/`.
- **Data/Storage**: Google Drive (integration logic will live in `backend/`).

## Tech Stack
- Python >= 3.12
- FastAPI & Uvicorn
- Streamlit
- Pydantic
- Pytest (Testing)
- Pyrefly (Static Type Checking)
- Poe the Poet (Task Runner)

## Global Constraints
- Decoupled backend and frontend.
- Strict type checking must be enforced.
- All development must happen on branches and be merged into `main` via PRs.
- Keep it simple: do not over-engineer the initial setup.

## Components & Tooling Setup

### 1. Project Structure
Create the following directories:
- `backend/`
- `frontend/`
- `tests/`
- `.github/workflows/`

### 2. Dependency Management
Update `pyproject.toml` to include:
- `streamlit`
- `pyrefly`

### 3. Task Management (Poe the Poet)
Configure tasks in `pyproject.toml`:
- `poe test`: Runs `pytest`
- `poe check`: Runs `pyrefly`
- `poe dev`: Concurrently runs `uvicorn` (backend) and `streamlit run` (frontend)

### 4. CI/CD Pipeline
Create a GitHub Actions workflow `.github/workflows/ci.yml` that:
- Triggers on push and pull requests to `main`.
- Installs dependencies using `uv` (as indicated by the `uv.lock` file in the repo).
- Runs `poe check`.
- Runs `poe test`.

### 5. Git Strategy & Setup
- Add a `.gitignore` tailored for Python, Pyrefly, and Streamlit.
- Initialize `TODO.md` tracking the implementation steps.

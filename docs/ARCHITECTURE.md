# Architecture & Technical Notes

This document covers the internals of Paper Butler: how the backend and Google
Drive integration work, AI metadata generation defaults, deployment/security caveats,
and dev tooling. See the [README](../README.md) for user-facing setup and quick-start
instructions.

## Components

- **Frontend** (`paper-butler/src/frontend/app.py`) — Streamlit UI. Renders the
  library sidebar (search, status/tag filters, bulk select), the upload flow, and the
  paper detail view (embedded PDF viewer + editable metadata form).
- **Backend** (`paper-butler/src/backend/`) — FastAPI app with three responsibilities:
  - `main.py` — serves cached PDFs to the Streamlit iframe via a `/papers` `StaticFiles`
    mount, plus a `GET /` welcome route.
  - `drive.py` — Google Drive integration: OAuth credential flow (load/cache/save),
    get-or-create root/library/paper folders, list/create libraries, upload/download
    files, and sync the per-library `id-mapping.json` index.
  - `huggingface_client.py` — PDF text extraction (`pypdf`), calls to Hugging Face
    Inference Providers with retry, structured parsing into a `GeneratedMetadata`
    Pydantic model, text embedding, and cosine-similarity duplicate detection
    (`find_similar_papers`).
  - `models.py` — frozen Pydantic models: `PaperMetadata`, `PaperIndexEntry`,
    `LibraryIndex` (status enum: Unread/Reading/Read/TODO).

Each library's papers and its `id-mapping.json` index live entirely in the user's own
Google Drive — there is no separate database.

## AI metadata generation defaults

By default, metadata generation uses `Qwen/Qwen2.5-7B-Instruct` for title/abstract/tag
drafting and `sentence-transformers/all-MiniLM-L6-v2` for embeddings. Duplicate-paper
warnings trigger at a cosine similarity threshold of 0.90. Usage is billed against your
Hugging Face plan's Inference Providers credits.

## Troubleshooting: corporate network / VPN TLS interception

If "Generate metadata" fails with:

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
```

your network is likely behind a TLS-inspecting proxy (e.g. Cato Networks, Zscaler,
Netskope) whose certificate your OS trusts but Python's bundled CA list (`certifi`)
does not. This project depends on `pip-system-certs`, which patches Python to trust the
OS's native certificate store instead — running `uv sync` should be enough to pick it
up. If the error persists, confirm the dependency installed with:

```bash
uv run python -c "import pip_system_certs"
```

## Security note: unauthenticated PDF endpoint

The FastAPI backend serves the `PAPERS_DIR` via an unauthenticated `StaticFiles`
endpoint. This is fine for `localhost`, but if deployed on a shared network or bound to
`0.0.0.0`, anyone can access the downloaded PDFs. Implement endpoint authentication
before any public deployment.

## Dev tooling

### Code Review Graph

```bash
code-review-graph install          # auto-detects and configures all supported platforms
code-review-graph build            # parse your codebase
```

### Pre-commit hooks

```bash
uvx prek install
uvx prek run --all-files

uvx pre-commit install
```

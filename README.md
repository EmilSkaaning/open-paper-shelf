# Open Paper Shelf

**Your research paper library, backed by your own Google Drive.**

Open Paper Shelf is a self-hosted app for organizing PDF research papers — no
separate database, no vendor lock-in. Your papers live in a Google Drive folder
you already own; this app just gives you a fast, searchable, AI-assisted shelf
on top of them.

## Why Open Paper Shelf

- **Your papers stay yours.** Everything is stored in your own Google Drive —
  no third-party database, no proprietary export format.
- **Drag-and-drop upload.** Add multiple PDFs at once, with per-file progress.
- **Instant search and filtering.** Find papers by title, reading status
  (Unread / Reading / Read / TODO), or tags as you type.
- **AI-generated metadata.** One click drafts a title, abstract/TL;DR, and tags
  from the PDF text using Hugging Face's hosted models — no GPU required.
- **Automatic duplicate detection.** Embedding-based similarity checks warn you
  when you're about to add a paper you already have.
- **Built-in PDF viewer.** Read the paper and edit its metadata side by side.

## Run It

1. Install dependencies:
   ```bash
   uv sync
   # or: pip install -e .
   ```
2. Run the backend (serves PDFs to the UI):
   ```bash
   uv run poe fastapi
   ```
3. In a separate terminal, run the frontend:
   ```bash
   uv run poe streamlit
   ```
4. Open the Streamlit URL it prints (typically `http://localhost:8501`) and
   click "Login with Google" to connect your own Google Drive.

### Optional: AI metadata generation

To enable the "Generate metadata" button, set a Hugging Face access token:

1. Sign in to [huggingface.co](https://huggingface.co) (free account works).
2. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) → **Create new token** → type **Fine-grained**.
3. Under permissions, enable **"Make calls to Inference Providers"** (no other scopes needed).
4. Copy the token (starts with `hf_...`) and export it before running the app:
   ```bash
   export HF_TOKEN=hf_...
   ```
   To persist it across terminal sessions on macOS (zsh), add it to `~/.zshrc`
   instead of re-exporting it each time.

Running into a certificate error on a corporate network? See the
[troubleshooting note in the architecture doc](docs/ARCHITECTURE.md#troubleshooting-corporate-network--vpn-tls-interception).

> Deploying beyond `localhost`? Read the
> [security note](docs/ARCHITECTURE.md#security-note-unauthenticated-pdf-endpoint)
> about the PDF endpoint first.

## Develop On It

```bash
uv sync                # install dependencies (incl. dev tools)
uv run poe test        # run the full test suite
uv run poe check       # ruff format/lint, pyrefly, vulture, skylos
```

`test-backend` and `test-frontend` run scoped, coverage-tracked subsets of the
suite. See [AGENTS.md](AGENTS.md) for this repo's full commit workflow and
coding standards, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for backend
internals, AI model defaults, and dev tooling (code-review-graph, pre-commit).

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

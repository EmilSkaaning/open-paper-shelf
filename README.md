# Open Paper Shelf

A front-end for papers backed by Google Drive.

## Running the App

1. Ensure you have your `credentials.json` configured as a Web Application in the Google Cloud Console (with redirect URI `http://localhost:8501/`) and placed in the root of the project.
2. Install the project dependencies (e.g., using `uv sync` or `pip install -e .`).
3. Run the FastAPI backend server (this serves the PDFs for the Streamlit UI):
   ```bash
   uv run poe fastapi
   # or: uv run fastapi dev open-paper-shelf/src/backend/main.py --port 8000
   ```

> [!WARNING]
> **Security Note:** The FastAPI backend serves the `PAPERS_DIR` via an unauthenticated `StaticFiles` endpoint. While acceptable for `localhost`, if deployed on a shared network or bound to `0.0.0.0`, anyone can access the downloaded PDFs. Implement endpoint authentication before a public deployment.
4. In a separate terminal, run the Streamlit frontend locally:
   ```bash
   uv run poe streamlit
   # or: uv run streamlit run open-paper-shelf/src/frontend/app.py
   ```

## Hugging Face Metadata Generation

Each paper's detail view has an on-demand "Generate metadata" button that
suggests a title, abstract/TL;DR, and tags from the PDF's text, and computes
an embedding used to warn about likely duplicate papers already in the
library. It calls Hugging Face's hosted Inference Providers API — no local
model download or GPU required.

Setup:
1. Sign in to [huggingface.co](https://huggingface.co) (create a free account if you don't have one).
2. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) and click **"Create new token"**.
3. Choose the **"Fine-grained"** token type.
4. Under permissions, scroll to the **Inference** section and enable **"Make calls to Inference Providers"**. No other scopes are needed.
5. Give it a name (e.g. `open-paper-shelf`) and click **"Create token"**, then copy the value (starts with `hf_...`) — it's only shown once.
6. Export it in the same terminal you'll run the app from, before `uv run poe fastapi` / `uv run poe streamlit`:
   ```bash
   export HF_TOKEN=hf_...
   ```
   This only lasts for the current terminal session. To persist it across terminal sessions on macOS (zsh), append it to `~/.zshrc` and reload:
   ```bash
   echo 'export HF_TOKEN=hf_...' >> ~/.zshrc
   source ~/.zshrc
   ```
   (Use `>>`, not `>` — a single `>` overwrites the whole file. If you'd rather not have the token sitting in your shell history from the `echo` command, open `~/.zshrc` in an editor instead, e.g. `nano ~/.zshrc`, add the line manually, save, then run `source ~/.zshrc`.) Verify it's set with `echo $HF_TOKEN`.

   (Same convention as the existing `FASTAPI_URL` environment variable — no `.env` file or secrets store is used.)

By default this uses `Qwen/Qwen2.5-7B-Instruct` for title/abstract/tag
generation and `sentence-transformers/all-MiniLM-L6-v2` for embeddings.
Usage is billed against your Hugging Face plan's Inference Providers
credits.

> [!NOTE]
> **Corporate network / VPN users:** if "Generate metadata" fails with
> `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed
> certificate in certificate chain`, your network is likely behind a
> TLS-inspecting proxy (e.g. Cato Networks, Zscaler, Netskope) whose
> certificate your OS trusts but Python's bundled CA list (`certifi`) does
> not. This project depends on `pip-system-certs`, which patches Python to
> trust the OS's native certificate store instead — running `uv sync`
> should be enough to pick it up. If the error persists, confirm the
> dependency installed with `uv run python -c "import pip_system_certs"`.

## Tools & Utilities

### Code Review Graph
To set up and run `code-review-graph`:
```bash
code-review-graph install          # auto-detects and configures all supported platforms
code-review-graph build            # parse your codebase
```

### Pre-commit Hooks
To set up and run `prek`:
```bash
uvx prek install
uvx prek run --all-files

uvx pre-commit install
```

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

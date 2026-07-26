# Open Paper Shelf

A front-end for papers backed by Google Drive.

## Running the App

1. Ensure you have your `credentials.json` configured as a Web Application in the Google Cloud Console (with redirect URI `http://localhost:8501/`) and placed in the root of the project.
2. Install the project dependencies (e.g., using `uv sync` or `pip install -e .`).
3. Run the Streamlit frontend locally:
   ```bash
   streamlit run open-paper-shelf/src/frontend/app.py
   ```

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
```

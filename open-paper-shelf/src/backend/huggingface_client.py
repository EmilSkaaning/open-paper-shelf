"""Hugging Face Inference Providers client for on-demand paper metadata generation."""

import os
import time
from pathlib import Path
from typing import Callable, List, Literal, Optional, Sequence, TypeVar

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel, Field
from pypdf import PdfReader

from backend.models import LibraryIndex

T = TypeVar("T")

DEFAULT_GENERATION_MODEL: str = "Qwen/Qwen2.5-7B-Instruct"
DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM: int = 384
MAX_EXTRACTED_CHARS: int = 6000
MAX_RETRIES: int = 3
RETRY_DELAY_SECONDS: float = 2.0
DEFAULT_DUPLICATE_THRESHOLD: float = 0.90
MAX_TAGS: int = 8


class HFTokenMissingError(RuntimeError):
    """Raised when no Hugging Face API token is available to make a request.

    The token must have the "Make calls to Inference Providers" scope.
    """


class GeneratedMetadata(BaseModel):
    """Hugging Face-generated metadata for a paper, staged for user review.

    Attributes:
        title: Suggested paper title.
        abstract: Suggested abstract/TL;DR.
        tags: Suggested tag strings.
    """

    title: str
    abstract: str
    tags: List[str] = Field(default_factory=list)


def extract_pdf_text(pdf_path: Path, max_chars: int = MAX_EXTRACTED_CHARS) -> str:
    """Extracts text from a PDF file for use as model input.

    Args:
        pdf_path: Path to the local PDF file.
        max_chars: Maximum number of characters to return, truncating any
            excess so requests stay within model context limits.

    Returns:
        The concatenated text of every page, truncated to `max_chars`.
        Returns an empty string if the PDF has no extractable text (e.g. a
        scanned/image-only document) rather than raising.

    Raises:
        ValueError: If the PDF file cannot be read/parsed (e.g. corrupt file).
    """
    try:
        reader = PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        raise ValueError(f"Could not read PDF: {e}") from e
    return text[:max_chars]


def get_inference_client(token: Optional[str] = None) -> InferenceClient:
    """Builds a Hugging Face InferenceClient, resolving the API token.

    Args:
        token: An explicit Hugging Face API token. If not provided, falls
            back to the `HF_TOKEN` environment variable.

    Returns:
        A configured InferenceClient.

    Raises:
        HFTokenMissingError: If neither `token` nor the `HF_TOKEN`
            environment variable is set.
    """
    resolved = token or os.environ.get("HF_TOKEN")
    if not resolved:
        raise HFTokenMissingError(
            "No Hugging Face API token found. Set the HF_TOKEN environment "
            'variable to a fine-grained token with the "Make calls to '
            'Inference Providers" scope.'
        )
    return InferenceClient(token=resolved)


def _call_with_retry(
    fn: Callable[[], T],
    max_retries: int = MAX_RETRIES,
    delay_seconds: float = RETRY_DELAY_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Retries a Hugging Face API call on transient 503 "model loading" errors.

    Args:
        fn: A zero-argument callable making the API request.
        max_retries: Maximum number of attempts before giving up.
        delay_seconds: Delay passed to `sleep_fn` between retries.
        sleep_fn: Called between retries; injected so tests never sleep for
            real.

    Returns:
        The return value of `fn` on success.

    Raises:
        Exception: Re-raises the last exception if all attempts fail, or
            immediately for any non-503 error.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except HfHubHTTPError as e:
            status_code = getattr(e.response, "status_code", None)
            if status_code != 503:
                raise
            last_error = e
            if attempt < max_retries - 1:
                sleep_fn(delay_seconds)
    assert last_error is not None
    raise last_error


def _build_prompt_messages(
    kind: Literal["title", "abstract", "tags"], pdf_text: str
) -> List[dict]:
    """Builds the chat messages for one metadata-generation subtask.

    Args:
        kind: Which field to generate.
        pdf_text: Extracted paper text to generate from.

    Returns:
        A list of `{"role": ..., "content": ...}` messages suitable for
        `InferenceClient.chat_completion`.
    """
    instructions = {
        "title": (
            "You are a scientific paper assistant. Respond with only a "
            "short, accurate title for the paper below - no quotes, no "
            "preamble, no extra commentary."
        ),
        "abstract": (
            "You are a scientific paper assistant. Write a concise "
            "abstract/TL;DR (2-4 sentences) summarizing the paper below. "
            "Respond with only the summary - no preamble."
        ),
        "tags": (
            "You are a scientific paper assistant. Respond with only a "
            "comma-separated list of up to 8 short topical tags for the "
            "paper below - no preamble, no numbering."
        ),
    }
    return [
        {"role": "system", "content": instructions[kind]},
        {"role": "user", "content": pdf_text},
    ]


def generate_paper_metadata(
    pdf_text: str,
    model: str = DEFAULT_GENERATION_MODEL,
    client: Optional[InferenceClient] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GeneratedMetadata:
    """Generates a title, abstract, and tags for a paper via Hugging Face.

    Args:
        pdf_text: Extracted paper text to generate from.
        model: The chat/instruct model to call.
        client: An existing InferenceClient to reuse. If not provided, one
            is created via `get_inference_client()`.
        sleep_fn: Passed through to the retry helper for each subtask call.

    Returns:
        A GeneratedMetadata with the suggested title, abstract, and tags.

    Raises:
        HFTokenMissingError: If no client is given and no HF token is set.
        Exception: Propagates any Hugging Face API error surviving retries.
    """
    active_client = client or get_inference_client()

    def call(kind: Literal["title", "abstract", "tags"]) -> str:
        response = _call_with_retry(
            lambda: active_client.chat_completion(
                model=model, messages=_build_prompt_messages(kind, pdf_text)
            ),
            sleep_fn=sleep_fn,
        )
        return (response.choices[0].message.content or "").strip()

    title = call("title")
    abstract = call("abstract")
    tags_raw = call("tags")

    seen: set[str] = set()
    tags: List[str] = []
    for tag in tags_raw.split(","):
        stripped = tag.strip()
        if stripped and stripped.lower() not in seen:
            seen.add(stripped.lower())
            tags.append(stripped)
        if len(tags) >= MAX_TAGS:
            break

    return GeneratedMetadata(title=title, abstract=abstract, tags=tags)


def embed_text(
    text: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
    client: Optional[InferenceClient] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> List[float]:
    """Computes a sentence embedding for the given text via Hugging Face.

    Args:
        text: The text to embed.
        model: The embedding model to call.
        client: An existing InferenceClient to reuse. If not provided, one
            is created via `get_inference_client()`.
        sleep_fn: Passed through to the retry helper.

    Returns:
        A flat list of `EMBEDDING_DIM` floats.

    Raises:
        HFTokenMissingError: If no client is given and no HF token is set.
        ValueError: If the response cannot be normalized to a vector of the
            expected dimension.
        Exception: Propagates any Hugging Face API error surviving retries.
    """
    active_client = client or get_inference_client()
    result = _call_with_retry(
        lambda: active_client.feature_extraction(text, model=model),
        sleep_fn=sleep_fn,
    )
    vector = result.tolist() if hasattr(result, "tolist") else list(result)

    # Mean-pool a per-token (2D) response down to a single sentence vector,
    # in pure Python so this module doesn't need its own numpy dependency.
    while vector and isinstance(vector[0], list):
        rows = vector
        vector = [sum(col) / len(rows) for col in zip(*rows)]

    if len(vector) != EMBEDDING_DIM:
        raise ValueError(
            f"Expected a {EMBEDDING_DIM}-dim embedding, got {len(vector)} "
            f"from model {model!r}."
        )
    return vector


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Computes the cosine similarity between two vectors.

    Args:
        a: The first vector.
        b: The second vector.

    Returns:
        A similarity score in [-1, 1], or 0.0 if either vector is empty or
        has zero norm (the expected "no embedding yet" case).

    Raises:
        ValueError: If `a` and `b` have different lengths.
    """
    if len(a) != len(b):
        raise ValueError("vectors must be the same length")
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_similar_papers(
    embedding: Sequence[float],
    index: LibraryIndex,
    exclude_pid: Optional[str] = None,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> List[tuple]:
    """Finds papers in the library whose embedding is similar to `embedding`.

    Args:
        embedding: The embedding to compare against every paper in `index`.
        index: The library index to scan.
        exclude_pid: A paper ID to skip (typically the paper being generated
            for, so it's never flagged as its own duplicate).
        threshold: Minimum cosine similarity score to be considered a match.

    Returns:
        A list of `(paper_id, title, score)` tuples for every paper at or
        above `threshold`, sorted by score descending. Papers with no
        stored embedding yet are excluded.
    """
    matches = []
    for pid, entry in index.papers.items():
        if pid == exclude_pid or not entry.embedding:
            continue
        score = cosine_similarity(embedding, entry.embedding)
        if score >= threshold:
            matches.append((pid, entry.title, score))
    matches.sort(key=lambda m: m[2], reverse=True)
    return matches

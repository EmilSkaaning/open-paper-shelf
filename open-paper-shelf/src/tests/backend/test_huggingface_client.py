"""Unit tests for backend.huggingface_client."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError
from pytest_mock import MockerFixture

import backend.huggingface_client as huggingface_client
from backend.huggingface_client import (
    EMBEDDING_DIM,
    HFTokenMissingError,
    _build_combined_prompt_messages,
    _call_with_retry,
    _clean_extracted_text,
    _extract_json_object,
    cosine_similarity,
    embed_text,
    extract_pdf_text,
    find_similar_papers,
    generate_paper_metadata,
    get_inference_client,
)
from backend.models import LibraryIndex, PaperIndexEntry


def _make_503_error() -> HfHubHTTPError:
    """Builds an HfHubHTTPError shaped like a 503 "model loading" response."""
    response = httpx.Response(
        status_code=503, request=httpx.Request("POST", "https://example.com")
    )
    return HfHubHTTPError("Model is loading", response=response)


def _make_500_error() -> HfHubHTTPError:
    """Builds an HfHubHTTPError shaped like a non-retryable server error."""
    response = httpx.Response(
        status_code=500, request=httpx.Request("POST", "https://example.com")
    )
    return HfHubHTTPError("Internal server error", response=response)


def _make_chat_response(content: str) -> MagicMock:
    """Builds a MagicMock standing in for a ChatCompletionOutput."""
    response = MagicMock()
    response.choices[0].message.content = content
    return response


class TestExtractPdfText:
    """Test suite for extract_pdf_text."""

    def test_extracts_and_joins_page_text(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test text from multiple pages is concatenated."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        page1, page2 = MagicMock(), MagicMock()
        page1.extract_text.return_value = "Page one."
        page2.extract_text.return_value = "Page two."
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            return_value=MagicMock(pages=[page1, page2]),
        )
        assert extract_pdf_text(pdf_path) == "Page one.\nPage two."

    def test_truncates_to_max_chars(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test the returned text is truncated to max_chars."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        page = MagicMock()
        page.extract_text.return_value = "a" * 100
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            return_value=MagicMock(pages=[page]),
        )
        assert len(extract_pdf_text(pdf_path, max_chars=10)) == 10

    def test_returns_empty_string_when_no_extractable_text(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a scanned/image-only PDF (no extractable text) returns ""."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        page = MagicMock()
        page.extract_text.return_value = None
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            return_value=MagicMock(pages=[page]),
        )
        assert extract_pdf_text(pdf_path) == ""

    def test_corrupt_pdf_raises_value_error(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test a PdfReader failure is wrapped as a ValueError."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"not a pdf")
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            side_effect=Exception("bad file"),
        )
        with pytest.raises(ValueError, match="Could not read PDF"):
            extract_pdf_text(pdf_path)

    def test_stops_reading_pages_once_max_chars_reached(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test pages after max_chars is reached are never extracted, so a
        long document isn't fully parsed just to be truncated away."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        page1 = MagicMock()
        page1.extract_text.return_value = "a" * 10
        page2 = MagicMock()
        page2.extract_text.return_value = "b" * 10
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            return_value=MagicMock(pages=[page1, page2]),
        )
        assert extract_pdf_text(pdf_path, max_chars=5) == "a" * 5
        page2.extract_text.assert_not_called()

    def test_cleans_before_truncating(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Test cleanup runs before max_chars truncation, so noise removed
        by cleaning doesn't eat into the content budget."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        page = MagicMock()
        page.extract_text.return_value = "infor-\nmation   here" + " " * 20 + "tail"
        mocker.patch(
            "backend.huggingface_client.PdfReader",
            return_value=MagicMock(pages=[page]),
        )
        result = extract_pdf_text(pdf_path, max_chars=100)
        assert result == "information here tail"


class TestCleanExtractedText:
    """Test suite for _clean_extracted_text."""

    def test_rejoins_hyphenated_line_wraps(self) -> None:
        """Test a word split across a line-wrap hyphen is rejoined."""
        assert _clean_extracted_text("infor-\nmation") == "information"

    def test_does_not_join_a_real_trailing_hyphen(self) -> None:
        """Test a hyphen followed by whitespace/punctuation is left alone."""
        assert _clean_extracted_text("well-\n\nknown") == "well-\n\nknown"

    def test_strips_references_section(self) -> None:
        """Test a References section heading and everything after it is
        removed, while earlier body text survives."""
        body = "Introduction text. " * 30
        text = f"{body}\nReferences\n[1] Some citation.\n[2] Another one."
        result = _clean_extracted_text(text)
        assert "Introduction text." in result
        assert "References" not in result
        assert "Some citation" not in result

    def test_strips_bibliography_section_case_insensitively(self) -> None:
        """Test a Bibliography heading is matched regardless of case."""
        body = "Body content. " * 40
        text = f"{body}\nBIBLIOGRAPHY\n[1] Citation."
        result = _clean_extracted_text(text)
        assert "Body content." in result
        assert "Citation" not in result

    def test_does_not_strip_references_mentioned_early_in_body(self) -> None:
        """Test the word 'references' near the very start of the text
        (e.g. in an abstract) doesn't trigger a false-positive truncation."""
        text = "This paper cross-references prior work.\nMore content follows."
        result = _clean_extracted_text(text)
        assert "More content follows" in result

    def test_collapses_excess_whitespace(self) -> None:
        """Test runs of spaces/tabs and blank lines are collapsed."""
        text = "Para one.\n\n\n\n\nPara   two.\t\tend."
        result = _clean_extracted_text(text)
        assert result == "Para one.\n\nPara two. end."

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        """Test the cleaned result has no leading/trailing whitespace."""
        assert _clean_extracted_text("  \n padded text \n  ") == "padded text"


class TestGetInferenceClient:
    """Test suite for get_inference_client."""

    def test_raises_when_no_token_and_no_env_var(self, mocker: MockerFixture) -> None:
        """Test HFTokenMissingError is raised with no token or env var."""
        mocker.patch.dict("os.environ", {}, clear=True)
        with pytest.raises(HFTokenMissingError, match="HF_TOKEN"):
            get_inference_client()

    def test_uses_explicit_token_over_env_var(self, mocker: MockerFixture) -> None:
        """Test an explicit token takes precedence over the env var."""
        mocker.patch.dict("os.environ", {"HF_TOKEN": "env-token"})
        mock_client_cls = mocker.patch("backend.huggingface_client.InferenceClient")
        get_inference_client(token="explicit-token")
        mock_client_cls.assert_called_once_with(token="explicit-token")

    def test_falls_back_to_env_var(self, mocker: MockerFixture) -> None:
        """Test the HF_TOKEN env var is used when no explicit token given."""
        mocker.patch.dict("os.environ", {"HF_TOKEN": "env-token"}, clear=True)
        mock_client_cls = mocker.patch("backend.huggingface_client.InferenceClient")
        get_inference_client()
        mock_client_cls.assert_called_once_with(token="env-token")


class TestCallWithRetry:
    """Test suite for _call_with_retry."""

    def test_succeeds_first_try_without_sleeping(self) -> None:
        """Test a successful call never invokes sleep_fn."""
        sleep_fn = MagicMock()
        fn = MagicMock(return_value="ok")
        assert _call_with_retry(fn, sleep_fn=sleep_fn) == "ok"
        sleep_fn.assert_not_called()
        assert fn.call_count == 1

    def test_retries_on_503_then_succeeds(self) -> None:
        """Test a 503 is retried until the call succeeds."""
        sleep_fn = MagicMock()
        fn = MagicMock(side_effect=[_make_503_error(), _make_503_error(), "ok"])
        result = _call_with_retry(fn, max_retries=3, sleep_fn=sleep_fn)
        assert result == "ok"
        assert fn.call_count == 3
        assert sleep_fn.call_count == 2

    def test_exhausts_retries_and_reraises(self) -> None:
        """Test the last 503 is re-raised once retries are exhausted."""
        sleep_fn = MagicMock()
        fn = MagicMock(side_effect=[_make_503_error(), _make_503_error()])
        with pytest.raises(HfHubHTTPError):
            _call_with_retry(fn, max_retries=2, sleep_fn=sleep_fn)
        assert fn.call_count == 2
        assert sleep_fn.call_count == 1

    def test_non_503_error_is_not_retried(self) -> None:
        """Test a non-503 error is raised immediately without retrying."""
        sleep_fn = MagicMock()
        fn = MagicMock(side_effect=_make_500_error())
        with pytest.raises(HfHubHTTPError):
            _call_with_retry(fn, sleep_fn=sleep_fn)
        assert fn.call_count == 1
        sleep_fn.assert_not_called()


class TestBuildCombinedPromptMessages:
    """Test suite for _build_combined_prompt_messages."""

    def test_builds_system_and_user_messages(self) -> None:
        """Test a system+user message pair is produced with the paper text."""
        messages = _build_combined_prompt_messages("paper body text")
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["content"] == "paper body text"
        assert messages[0]["content"]

    def test_instruction_asks_for_title_abstract_tags_json(self) -> None:
        """Test the instruction names all three JSON keys to produce."""
        messages = _build_combined_prompt_messages("paper body text")
        content = messages[0]["content"]
        assert '"title"' in content
        assert '"abstract"' in content
        assert '"tags"' in content

    def test_lists_existing_tags_when_given(self) -> None:
        """Test the instruction names existing tags to bias reuse."""
        messages = _build_combined_prompt_messages(
            "paper body text", existing_tags=["nlp", "vision"]
        )
        assert "nlp, vision" in messages[0]["content"]

    def test_omits_existing_tags_clause_when_empty(self) -> None:
        """Test no existing-tags clause is added when none are given."""
        messages = _build_combined_prompt_messages("paper body text")
        assert "Prefer reusing" not in messages[0]["content"]


class TestExtractJsonObject:
    """Test suite for _extract_json_object."""

    def test_returns_plain_json_unchanged(self) -> None:
        """Test a bare JSON object is returned as-is."""
        assert _extract_json_object('{"title": "T"}') == '{"title": "T"}'

    def test_strips_json_code_fence(self) -> None:
        """Test a ```json fenced object has the fence stripped."""
        content = '```json\n{"title": "T"}\n```'
        assert _extract_json_object(content) == '{"title": "T"}'

    def test_strips_plain_code_fence(self) -> None:
        """Test a fence with no language tag is also stripped."""
        content = '```\n{"title": "T"}\n```'
        assert _extract_json_object(content) == '{"title": "T"}'

    def test_narrows_to_braces_despite_stray_preamble(self) -> None:
        """Test text before/after the JSON object is discarded."""
        content = 'Sure, here you go:\n{"title": "T"}\nHope that helps!'
        assert _extract_json_object(content) == '{"title": "T"}'

    def test_returns_stripped_input_when_no_braces_found(self) -> None:
        """Test non-JSON input is returned stripped, not raising here."""
        assert _extract_json_object("  not json at all  ") == "not json at all"


class TestGeneratePaperMetadata:
    """Test suite for generate_paper_metadata."""

    def test_returns_generated_metadata_from_single_json_call(self) -> None:
        """Test title/abstract/tags are all sourced from one JSON response."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response(
            '{"title": "Attention Is All You Need", '
            '"abstract": "Introduces the Transformer architecture.", '
            '"tags": ["nlp", "transformers", "attention"]}'
        )
        result = generate_paper_metadata(
            "paper text", client=client, sleep_fn=MagicMock()
        )
        assert result.title == "Attention Is All You Need"
        assert result.abstract == "Introduces the Transformer architecture."
        assert result.tags == ["nlp", "transformers", "attention"]
        assert client.chat_completion.call_count == 1

    def test_calls_with_temperature_zero(self) -> None:
        """Test the chat call is made with temperature=0 for determinism."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response(
            '{"title": "T", "abstract": "A", "tags": []}'
        )
        generate_paper_metadata("paper text", client=client, sleep_fn=MagicMock())
        assert client.chat_completion.call_args.kwargs["temperature"] == 0

    def test_parses_json_array_tags_deduped_and_capped(self) -> None:
        """Test tags are stripped, deduped case-insensitively, and capped at 8."""
        client = MagicMock()
        many_tags = [f"tag{i}" for i in range(10)] + ["Tag0", " tag1 "]
        client.chat_completion.return_value = _make_chat_response(
            json.dumps({"title": "Title", "abstract": "Abstract", "tags": many_tags})
        )
        result = generate_paper_metadata(
            "paper text", client=client, sleep_fn=MagicMock()
        )
        assert result.tags == [f"tag{i}" for i in range(8)]

    def test_tolerates_non_list_tags_field(self) -> None:
        """Test a malformed (non-list) tags field is treated as empty."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response(
            '{"title": "T", "abstract": "A", "tags": "not a list"}'
        )
        result = generate_paper_metadata(
            "paper text", client=client, sleep_fn=MagicMock()
        )
        assert result.tags == []

    def test_raises_value_error_on_malformed_json(self) -> None:
        """Test a non-JSON response raises ValueError with the content."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response("not json at all")
        with pytest.raises(ValueError, match="non-JSON response"):
            generate_paper_metadata("paper text", client=client, sleep_fn=MagicMock())

    @pytest.mark.parametrize("content", ['["title", "abstract"]', '"just a string"'])
    def test_raises_value_error_on_non_object_json(self, content: str) -> None:
        """Test valid JSON that isn't an object raises ValueError, not AttributeError."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response(content)
        with pytest.raises(ValueError, match="isn't an object"):
            generate_paper_metadata("paper text", client=client, sleep_fn=MagicMock())

    def test_passes_existing_tags_through_to_build_combined_prompt_messages(
        self, mocker: MockerFixture
    ) -> None:
        """Test existing_tags reaches the prompt builder for the single call."""
        client = MagicMock()
        client.chat_completion.return_value = _make_chat_response(
            '{"title": "T", "abstract": "A", "tags": ["nlp"]}'
        )
        build_spy = mocker.spy(huggingface_client, "_build_combined_prompt_messages")

        generate_paper_metadata(
            "paper text",
            client=client,
            sleep_fn=MagicMock(),
            existing_tags=["nlp", "vision"],
        )

        assert build_spy.call_args.args[1] == ["nlp", "vision"]

    def test_propagates_hf_token_missing_error(self, mocker: MockerFixture) -> None:
        """Test the error from get_inference_client() propagates when no client given."""
        mocker.patch(
            "backend.huggingface_client.get_inference_client",
            side_effect=HFTokenMissingError("no token"),
        )
        with pytest.raises(HFTokenMissingError):
            generate_paper_metadata("paper text")

    def test_propagates_error_after_retries_exhausted(self) -> None:
        """Test a persistent 503 propagates out of generate_paper_metadata."""
        client = MagicMock()
        client.chat_completion.side_effect = _make_503_error()
        with pytest.raises(HfHubHTTPError):
            generate_paper_metadata("paper text", client=client, sleep_fn=MagicMock())


class TestEmbedText:
    """Test suite for embed_text."""

    def test_returns_flat_vector_as_is(self) -> None:
        """Test a flat (already 1D) response is returned unchanged."""
        client = MagicMock()
        client.feature_extraction.return_value = [0.1] * EMBEDDING_DIM
        result = embed_text("some text", client=client, sleep_fn=MagicMock())
        assert result == [0.1] * EMBEDDING_DIM

    def test_mean_pools_2d_response_to_expected_dim(self) -> None:
        """Test a per-token (2D) response is mean-pooled down to 1D."""
        client = MagicMock()
        client.feature_extraction.return_value = [
            [1.0] * EMBEDDING_DIM,
            [3.0] * EMBEDDING_DIM,
        ]
        result = embed_text("some text", client=client, sleep_fn=MagicMock())
        assert result == [2.0] * EMBEDDING_DIM

    def test_unwraps_batch_dim_before_mean_pooling(self) -> None:
        """Test a batched per-token (3D) response - a list containing one
        2D per-token matrix - has the batch dim stripped before pooling,
        instead of crashing in zip(*rows)."""
        client = MagicMock()
        client.feature_extraction.return_value = [
            [
                [1.0] * EMBEDDING_DIM,
                [3.0] * EMBEDDING_DIM,
            ]
        ]
        result = embed_text("some text", client=client, sleep_fn=MagicMock())
        assert result == [2.0] * EMBEDDING_DIM

    def test_uses_tolist_for_ndarray_like_response(self) -> None:
        """Test an ndarray-like response is converted via .tolist()."""
        client = MagicMock()
        fake_array = MagicMock()
        fake_array.tolist.return_value = [0.5] * EMBEDDING_DIM
        client.feature_extraction.return_value = fake_array
        result = embed_text("some text", client=client, sleep_fn=MagicMock())
        assert result == [0.5] * EMBEDDING_DIM

    def test_raises_value_error_on_unexpected_dimension(self) -> None:
        """Test a wrong-length vector raises ValueError."""
        client = MagicMock()
        client.feature_extraction.return_value = [0.1, 0.2, 0.3]
        with pytest.raises(ValueError, match="Expected a"):
            embed_text("some text", client=client, sleep_fn=MagicMock())


class TestCosineSimilarity:
    """Test suite for cosine_similarity."""

    def test_identical_vectors_score_one(self) -> None:
        """Test cosine similarity of a vector with itself is 1.0."""
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        """Test orthogonal vectors score 0.0."""
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_empty_vector_returns_zero_without_dividing_by_zero(self) -> None:
        """Test an empty vector returns 0.0 rather than raising."""
        assert cosine_similarity([], []) == 0.0

    def test_zero_norm_vector_returns_zero(self) -> None:
        """Test an all-zero vector returns 0.0 rather than raising."""
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_mismatched_length_raises_value_error(self) -> None:
        """Test vectors of different lengths raise ValueError."""
        with pytest.raises(ValueError, match="same length"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_precomputed_norm_a_matches_computed_norm_a(self) -> None:
        """Test passing a pre-computed norm_a gives the same score as
        letting cosine_similarity compute it itself."""
        a, b = [1.0, 2.0, 3.0], [3.0, 2.0, 1.0]
        norm_a = sum(x * x for x in a) ** 0.5
        assert cosine_similarity(a, b, norm_a=norm_a) == cosine_similarity(a, b)

    def test_precomputed_zero_norm_a_returns_zero(self) -> None:
        """Test a pre-computed zero norm_a is honored rather than
        recomputed from `a`."""
        assert cosine_similarity([1.0, 2.0], [1.0, 2.0], norm_a=0.0) == 0.0


def _index_with(entries: dict[str, PaperIndexEntry]) -> LibraryIndex:
    """Builds a LibraryIndex with the given {pid: PaperIndexEntry} entries."""
    return LibraryIndex(papers=entries)


def _entry(title: str, embedding: list[float]) -> PaperIndexEntry:
    """Builds a minimal PaperIndexEntry with the given title/embedding."""
    return PaperIndexEntry(
        title=title,
        pdf_file_id="pdf",
        meta_file_id="meta",
        folder_id="folder",
        embedding=embedding,
    )


class TestFindSimilarPapers:
    """Test suite for find_similar_papers."""

    def test_finds_entries_above_threshold_sorted_descending(self) -> None:
        """Test matches are returned sorted by score descending."""
        index = _index_with(
            {
                "a": _entry("A", [1.0, 0.0]),
                "b": _entry("B", [0.99, 0.14]),
                "c": _entry("C", [0.0, 1.0]),
            }
        )
        matches = find_similar_papers([1.0, 0.0], index, threshold=0.9)
        assert [m[0] for m in matches] == ["a", "b"]
        assert matches[0][2] >= matches[1][2]

    def test_excludes_self_pid(self) -> None:
        """Test the excluded paper ID never appears in the results."""
        index = _index_with({"a": _entry("A", [1.0, 0.0])})
        matches = find_similar_papers([1.0, 0.0], index, exclude_pid="a", threshold=0.9)
        assert matches == []

    def test_excludes_entries_with_no_embedding_yet(self) -> None:
        """Test entries with an empty embedding are skipped."""
        index = _index_with({"a": _entry("A", [])})
        matches = find_similar_papers([1.0, 0.0], index, threshold=0.0)
        assert matches == []

    def test_returns_empty_list_when_nothing_above_threshold(self) -> None:
        """Test no matches are returned when nothing meets the threshold."""
        index = _index_with({"a": _entry("A", [0.0, 1.0])})
        matches = find_similar_papers([1.0, 0.0], index, threshold=0.9)
        assert matches == []

    def test_skips_entry_with_mismatched_embedding_dimension(self) -> None:
        """Test an entry whose embedding has a different length than the
        query embedding is skipped rather than raising, while other valid
        entries are still matched."""
        index = _index_with(
            {
                "a": _entry("A", [1.0, 0.0, 0.0]),
                "b": _entry("B", [1.0, 0.0]),
            }
        )
        matches = find_similar_papers([1.0, 0.0, 0.0], index, threshold=0.9)
        assert [m[0] for m in matches] == ["a"]

    def test_skips_entry_with_zero_norm_embedding(self) -> None:
        """Test an entry whose embedding is non-empty but all-zero is
        skipped rather than raising, while other valid entries still
        match."""
        index = _index_with(
            {
                "a": _entry("A", [0.0, 0.0]),
                "b": _entry("B", [1.0, 0.0]),
            }
        )
        matches = find_similar_papers([1.0, 0.0], index, threshold=0.9)
        assert [m[0] for m in matches] == ["b"]

    def test_empty_query_embedding_returns_empty_list(self) -> None:
        """Test an empty query embedding returns [] without raising."""
        index = _index_with({"a": _entry("A", [1.0, 0.0])})
        assert find_similar_papers([], index, threshold=0.9) == []

    def test_zero_norm_query_returns_empty_list_above_zero_threshold(self) -> None:
        """Test an all-zero query embedding returns [] when threshold > 0,
        since no entry could ever score above 0.0 against it."""
        index = _index_with({"a": _entry("A", [1.0, 0.0])})
        assert find_similar_papers([0.0, 0.0], index, threshold=0.9) == []

    def test_zero_norm_query_matches_at_zero_threshold(self) -> None:
        """Test an all-zero (or empty) query embedding still yields score-0.0
        matches when threshold <= 0.0, matching cosine_similarity's own
        zero-norm contract instead of always short-circuiting to []."""
        index = _index_with({"a": _entry("A", [1.0, 0.0])})
        matches = find_similar_papers([0.0, 0.0], index, threshold=0.0)
        assert matches == [("a", "A", 0.0)]

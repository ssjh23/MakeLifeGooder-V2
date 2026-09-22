"""BUILD STEP 5.4: the Claude adapter's prompt and response parsing.

Never talks to the real API: ``_client.messages.create`` is replaced with a
stub on every test, so the suite stays offline, free and deterministic. A
test whose expected output depends on a live model is a weather report, not a
test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from app.classify.llm.base import KNOWN_CATEGORY_SLUGS, LLMUnavailable
from app.classify.llm.claude import ClaudeAdapter
from app.config import get_settings


def _adapter() -> ClaudeAdapter:
    return ClaudeAdapter(get_settings())


def _response(*, stop_reason: str = "end_turn", merchants: list[dict[str, Any]]) -> object:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps({"merchants": merchants}))],
    )


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class TestBuildPrompt:
    def test_lists_every_known_category_and_every_descriptor(self) -> None:
        prompt = _adapter().build_prompt(["fairprice", "mcdonalds-junction8"])

        for slug in KNOWN_CATEGORY_SLUGS:
            assert slug in prompt
        assert "fairprice" in prompt
        assert "mcdonalds-junction8" in prompt

    def test_gives_the_model_an_explicit_way_to_decline(self) -> None:
        prompt = _adapter().build_prompt(["some-shop"])
        assert "omit" in prompt.lower()


class TestParse:
    def test_maps_a_successful_response_to_suggestions(self) -> None:
        response = _response(
            merchants=[
                {
                    "descriptor_key": "fairprice",
                    "canonical_name": "FairPrice",
                    "category_slug": "groceries",
                    "confidence": 0.95,
                }
            ]
        )

        suggestions = _adapter()._parse(response, requested=["fairprice"])

        assert len(suggestions) == 1
        assert suggestions[0].descriptor_key == "fairprice"
        assert suggestions[0].canonical_name == "FairPrice"
        assert suggestions[0].category_slug == "groceries"
        assert suggestions[0].confidence == 0.95

    def test_drops_an_entry_outside_the_request(self) -> None:
        """A model returning something not asked about is a signal, not data."""
        response = _response(
            merchants=[
                {
                    "descriptor_key": "unrequested-shop",
                    "canonical_name": "Unrequested Shop",
                    "category_slug": "other",
                    "confidence": 0.9,
                }
            ]
        )

        suggestions = _adapter()._parse(response, requested=["fairprice"])

        assert suggestions == []

    def test_a_shorter_response_is_never_padded(self) -> None:
        response = _response(merchants=[])

        suggestions = _adapter()._parse(response, requested=["fairprice", "some-new-shop"])

        assert suggestions == []

    def test_a_refusal_stop_reason_is_llm_unavailable_not_an_empty_result(self) -> None:
        response = _response(stop_reason="refusal", merchants=[])

        with pytest.raises(LLMUnavailable):
            _adapter()._parse(response, requested=["fairprice"])

    def test_malformed_json_is_llm_unavailable(self) -> None:
        response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="not json at all")],
        )

        with pytest.raises(LLMUnavailable):
            _adapter()._parse(response, requested=["fairprice"])

    def test_no_text_block_is_llm_unavailable(self) -> None:
        response = SimpleNamespace(stop_reason="end_turn", content=[])

        with pytest.raises(LLMUnavailable):
            _adapter()._parse(response, requested=["fairprice"])


class TestClassifyBatch:
    @pytest.mark.p1
    async def test_TC_REV_009_sixty_descriptors_produce_two_calls_not_sixty(self) -> None:
        adapter = _adapter()
        calls: list[list[str]] = []

        async def _fake_create(*, messages: list[dict[str, str]], **_: object) -> object:
            calls.append(messages)  # only the call count matters here
            return _response(merchants=[])

        adapter._client = SimpleNamespace(
            messages=SimpleNamespace(create=_fake_create)
        )  # type: ignore[assignment]

        descriptors = [f"shop-{i}" for i in range(60)]
        await adapter.classify_batch(descriptors)

        assert len(calls) == 2

    async def test_an_empty_batch_of_descriptors_makes_no_call(self) -> None:
        adapter = _adapter()
        called = False

        async def _fake_create(**_: object) -> object:
            nonlocal called
            called = True
            return _response(merchants=[])

        adapter._client = SimpleNamespace(
            messages=SimpleNamespace(create=_fake_create)
        )  # type: ignore[assignment]

        suggestions = await adapter.classify_batch([])

        assert suggestions == []
        assert called is False

    async def test_rate_limit_becomes_llm_unavailable(self) -> None:
        adapter = _adapter()
        request = _request()

        async def _fake_create(**_: object) -> object:
            raise anthropic.RateLimitError(
                "rate limited", response=httpx.Response(429, request=request), body=None
            )

        adapter._client = SimpleNamespace(
            messages=SimpleNamespace(create=_fake_create)
        )  # type: ignore[assignment]

        with pytest.raises(LLMUnavailable):
            await adapter.classify_batch(["fairprice"])

    async def test_a_provider_status_error_becomes_llm_unavailable(self) -> None:
        adapter = _adapter()
        request = _request()

        async def _fake_create(**_: object) -> object:
            raise anthropic.APIStatusError(
                "server error", response=httpx.Response(500, request=request), body=None
            )

        adapter._client = SimpleNamespace(
            messages=SimpleNamespace(create=_fake_create)
        )  # type: ignore[assignment]

        with pytest.raises(LLMUnavailable):
            await adapter.classify_batch(["fairprice"])

    async def test_a_connection_failure_becomes_llm_unavailable(self) -> None:
        adapter = _adapter()
        request = _request()

        async def _fake_create(**_: object) -> object:
            raise anthropic.APIConnectionError(request=request)

        adapter._client = SimpleNamespace(
            messages=SimpleNamespace(create=_fake_create)
        )  # type: ignore[assignment]

        with pytest.raises(LLMUnavailable):
            await adapter.classify_batch(["fairprice"])

"""Deterministic offline adapter.

The default everywhere except production, and the reason the whole suite runs
with no API key, no network and no cost.

More importantly it is *deterministic*. A test whose expected output depends on
a model's mood is not a test, it is a weather report. Every case that exercises
the cascade, the review board or the aggregates uses this, so those tests assert
real behaviour. The handful of tests that genuinely need the real provider say
so and are marked.
"""

from __future__ import annotations

import hashlib

from app.classify.llm.base import MerchantSuggestion

#: A small fixed taxonomy for tests. Keeping it small keeps expected values in
#: test cases readable.
FAKE_CATEGORIES = (
    "food-drink",
    "transport",
    "groceries",
    "shopping",
    "utilities",
    "entertainment",
    "health",
    "other",
)


class FakeLLMAdapter:
    """Assigns a category by hashing the descriptor.

    Arbitrary but stable: the same string always lands in the same category, on
    every machine and every run. That is the only property the tests need, and
    pretending to be clever here would make failures harder to read.
    """

    prompt_version = "fake-v1"

    def __init__(self, *, overrides: dict[str, str] | None = None) -> None:
        #: Fixed answers for descriptors a test cares about by name, so a case
        #: can say "FairPrice is groceries" without depending on the hash.
        self._overrides = overrides or {}
        self.calls: list[list[str]] = []

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        # Recorded so tests can assert batching behaviour: sixty unseen
        # descriptors must produce two calls, not sixty (TC-REV-009).
        self.calls.append(list(descriptor_keys))

        suggestions = []
        for key in descriptor_keys:
            category = self._overrides.get(key) or self._category_for(key)
            suggestions.append(
                MerchantSuggestion(
                    descriptor_key=key,
                    canonical_name=key.replace("-", " ").title(),
                    category_slug=category,
                    confidence=0.9,
                )
            )
        return suggestions

    @staticmethod
    def _category_for(key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return FAKE_CATEGORIES[digest[0] % len(FAKE_CATEGORIES)]


class FailingLLMAdapter:
    """Always unavailable.

    For the degradation tests: known merchants must still resolve from the
    cache, unknown ones queue, and no user sees a 500 (TC-REV-019).
    """

    prompt_version = "failing-v1"

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        from app.classify.llm.base import LLMUnavailable

        raise LLMUnavailable("Stub adapter is deliberately unavailable.")

"""Claude adapter.

The SDK wiring is written; the prompt is not. That split is deliberate. Client
construction, structured output, batching, retries and error mapping are
mechanical and easy to get subtly wrong. The prompt is the part that decides
whether a Singapore hawker centre is food or groceries, and it belongs to
whoever understands the data.

What the transport guarantees, so the prompt does not have to:

* only normalised descriptor strings are sent, never amounts, dates, identity
  or the PDF
* the response is schema-constrained, so a malformed answer is a provider error
  rather than a parsing puzzle
* a provider failure raises :class:`LLMUnavailable`, which the cascade treats as
  "these descriptors stay unresolved", never as a failed import

Note the two senses of "batch". This sends many descriptors in one request,
which is what keeps calls per statement to a handful. That is different from the
Batch API (``client.messages.batches``), which runs requests asynchronously at
half price and would fit a nightly reprocessing job rather than an interactive
import.

===========================================================================
BUILD STEP 5.4   depends on: nothing
Verify: uv run pytest tests/unit/classify/test_claude_adapter.py
===========================================================================
Independent of the rest of phase 5, so it can be done at any point after phase
0. Test it with a stubbed client, never the real API: the suite has to stay
offline and free, and a test whose expected output depends on a live model is a
weather report rather than a test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import anthropic

from app.classify.llm.base import LLMUnavailable, MerchantSuggestion
from app.telemetry import events, get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)

#: Schema the response is constrained to. Structured output means a malformed
#: reply is impossible rather than merely unlikely, so there is no defensive
#: parsing below.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "merchants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "descriptor_key": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "category_slug": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "descriptor_key",
                    "canonical_name",
                    "category_slug",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["merchants"],
    "additionalProperties": False,
}


class ClaudeAdapter:
    def __init__(self, settings: Settings) -> None:
        api_key = settings.anthropic_api_key
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key.get_secret_value() if api_key else None,
            # The SDK already retries 429 and 5xx with backoff. Custom retry
            # logic on top of it would compound the delays rather than improve
            # anything.
            max_retries=3,
        )
        self._model = settings.llm_model
        self._batch_size = settings.llm_batch_size
        self.prompt_version = settings.llm_prompt_version

    def build_prompt(self, descriptor_keys: list[str]) -> str:
        """Yours to write.

        TODO:
          1. State the task plainly: these are normalised merchant descriptors
             from Singapore bank statements, classify each one.
          2. List the available category slugs in the prompt and require the
             answer to come from that list. Without it the model invents
             plausible categories that match nothing in the database.
          3. Give the model an explicit way to decline. A descriptor it cannot
             place must come back absent or low-confidence, never guessed,
             because a guess is cached cross-tenant.
          4. Ask for a display name a person recognises, so ``fairprice``
             becomes "FairPrice" rather than the key.
          5. Say what confidence means, so it is calibrated rather than
             decorative. A low score routes the row to review.
          6. Include a handful of Singapore examples: a hawker centre, a
             transport operator, a telco. This is the whole reason a
             general-purpose categoriser is not enough.
          7. Bump ``LLM_PROMPT_VERSION`` whenever you change any of the above.
             That version is written onto every row this adapter classifies,
             and it is what makes "reprocess everything the old prompt touched"
             a query rather than a guess.
        """
        raise NotImplementedError("The classification prompt is not written yet.")

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        """Classify unseen descriptors, in chunks of the configured size."""
        suggestions: list[MerchantSuggestion] = []
        for start in range(0, len(descriptor_keys), self._batch_size):
            chunk = descriptor_keys[start : start + self._batch_size]
            suggestions.extend(await self._classify_chunk(chunk))
        return suggestions

    async def _classify_chunk(self, chunk: list[str]) -> list[MerchantSuggestion]:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=8000,
                # Low effort, thinking left on. Naming a merchant's category is
                # not a reasoning problem, so paying for depth here buys
                # nothing. Turning thinking off entirely is the worse trade: on
                # this model it can leak reasoning into the visible answer.
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
                messages=[{"role": "user", "content": self.build_prompt(chunk)}],
            )
        except anthropic.RateLimitError as exc:
            # Surfaced rather than absorbed: the cascade leaves these
            # descriptors unresolved and the import still completes.
            raise LLMUnavailable("Rate limited by the model provider.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"Provider returned {exc.status_code}.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("Could not reach the model provider.") from exc

        # Logged with the count only. The descriptors themselves are the one
        # thing that must not appear in a log line (ADR-014).
        logger.info(
            events.LLM_BATCH_DISPATCHED,
            batch_size=len(chunk),
            prompt_version=self.prompt_version,
        )

        return self._parse(response)

    def _parse(self, response: Any) -> list[MerchantSuggestion]:  # noqa: ANN401
        """Map the schema-constrained reply onto suggestions.

        TODO:
          1. Find the text block in ``response.content`` and load its JSON. The
             output is schema-constrained, so no defensive parsing is needed.
          2. Build a :class:`MerchantSuggestion` per entry.
          3. Drop any entry whose ``descriptor_key`` was not in the request. A
             model returning something you did not ask about is a signal, not
             data.
          4. Return a shorter list than the input when the model declined some,
             and **never** pad it. A fabricated suggestion is indistinguishable
             from a real one and would poison the shared alias cache for every
             tenant.
          5. Check ``response.stop_reason`` before reading content, and treat a
             refusal as :class:`LLMUnavailable` rather than as an empty result.
        """
        raise NotImplementedError

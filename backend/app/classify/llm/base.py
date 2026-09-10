"""The model provider seam.

The second of the worker's two Protocols. A stated success criterion is that the
system is not locked to one provider or one prompt, and this is where that is
kept true.

The interface is deliberately narrow: a list of normalised descriptor strings in,
a suggestion per descriptor out. Nothing about statements, amounts, users or
PDFs appears in the signature, which is the type system carrying a privacy rule.
The only thing that *can* cross this boundary is a merchant string.

``prompt_version`` is stored on every row the model classifies, so changing the
prompt lets you reprocess exactly those rows and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MerchantSuggestion:
    """One descriptor, classified."""

    descriptor_key: str
    canonical_name: str
    category_slug: str
    confidence: float


class LLMUnavailable(Exception):
    """The provider could not be reached or refused the request.

    Raised rather than swallowed, so the caller can leave those descriptors
    unresolved and carry on. An outage must degrade classification, never fail
    an import: rungs one to three still answer, and a statement whose new
    merchants are unclassified is far better than a statement that would not
    load (TC-REV-019).
    """


class LLMAdapter(Protocol):
    prompt_version: str

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        """Classify unseen descriptors.

        Batched, not per row. Cost and latency both follow the number of calls
        far more than the number of strings, and per-row calls would make cost
        scale with transaction volume, which is exactly the shape ADR-006
        rejected.

        Implementations must:

        * accept that the result may be shorter than the input, and never
          fabricate a suggestion to pad it
        * return categories from the known taxonomy rather than inventing names
        * be safe to retry, since the caller may retry the stage
        """
        ...

"""The descriptor normaliser.

The stated pain point of the whole project: ``MCDONALDS (CCP)`` and
``MCDONALDS-JUNCTION8`` must resolve to one merchant.

Three properties make this module what it is.

**It is a pure function.** No database, no network, no state. Given a string it
returns a string, always the same one. That is what lets the entire rule set be
tested against a fixture corpus in milliseconds, with no PDF and no fixtures
directory (TC-REV-004).

**It is standalone.** Improving the rules re-runs against stored descriptors
without re-parsing a single PDF. Bury this inside the parser and every
improvement costs a full reprocess of every statement anyone has ever uploaded.

**It is deterministic and debuggable.** This is the argument in ADR-006 against
embeddings. The problem is formatting noise, not meaning: outlet codes, casing,
punctuation, trailing branch names. Embeddings place ``MCDONALDS (CCP)`` near
``McDonalds`` and also near Burger King, because semantic proximity is the wrong
notion of similarity for merchant *identity*. And when a deterministic rule
merges two merchants wrongly, there is a rule to point at and fix; a wrong
cosine score offers nothing to change.

===========================================================================
BUILD STEP 0.2   depends on: nothing   blocks: 5.1 trigram rung, 5.3 cascade
Verify: uv run pytest tests/unit/classify/test_normalise.py
===========================================================================
Build the three helpers bottom-up, then compose them. Several corpus cases pass
on the first helper alone, so you get green early and can see which rule each
remaining failure needs.

Over-merging is the worse failure. A user seeing spending attributed to a shop
they have never visited stops trusting the whole dashboard; an extra question
during review is a mild annoyance. When in doubt, leave two merchants apart.
"""

from __future__ import annotations

import re
from typing import Final

#: Fragments that identify an outlet rather than a merchant. Starting points
#: from Singapore statements; extend from real data rather than imagination.
OUTLET_MARKERS: Final[tuple[str, ...]] = (
    "JUNCTION8",
    "CCP",
    "NEX",
    "VIVO",
    "TAMPINES",
    "ORCHARD",
    "JURONG",
    "WOODLANDS",
)

#: Payment-processor noise that appears around a merchant name and is not part
#: of it.
PROCESSOR_MARKERS: Final[tuple[str, ...]] = (
    "PAYNOW",
    "NETS",
    "GIRO",
    "VISA",
    "MASTERCARD",
    "PAYPAL *",
    "SQ *",
)

#: Trailing reference numbers, dates and store codes.
TRAILING_NOISE: Final[re.Pattern[str]] = re.compile(
    r"[\s\-#*]+(?:\d{4,}|[A-Z]{1,3}\d{2,})\s*$"
)


def normalise(raw: str) -> str:
    """Reduce a raw statement descriptor to a stable merchant key.

    ``"MCDONALDS-JUNCTION8"`` and ``"MCDONALDS (CCP)"`` both become
    ``"mcdonalds"``.

    The output is a key, not a display name: lowercase, punctuation collapsed,
    no outlet or processor fragments. The name a person sees comes from
    ``merchants.canonical_name``, so this can be aggressive about noise without
    making anything ugly on screen.

    TODO (write the helpers below first, then compose here):
      1. Return early for empty or whitespace-only input. A blank descriptor is
         a data problem, not a crash, and must survive as an unclassified row.
      2. Call the three helpers in order: processor prefixes, outlet codes,
         then whitespace and punctuation last so it cleans up what the others
         leave behind.
      3. Run the corpus and work through the failures one at a time.
      4. Check the two traps in the corpus deliberately. ``GRABFOOD`` must not
         become ``grab``: it is a different spending category, and keeping them
         apart matters more than tidiness. ``7-ELEVEN 2245`` must become
         ``7-eleven``: the digit and hyphen are part of the name, and only the
         trailing number is noise.

    See TC-REV-003, TC-REV-004.
    """
    raise NotImplementedError


def _strip_outlet_codes(value: str) -> str:
    """Remove the branch or mall a transaction happened at.

    TODO:
      1. Strip a bracketed fragment at the end, as in ``MCDONALDS (CCP)``.
      2. Strip an :data:`OUTLET_MARKERS` fragment after a hyphen or space.
      3. Apply :data:`TRAILING_NOISE` for store numbers and references.
      4. Do not strip a leading digit-hyphen, or ``7-ELEVEN`` loses its name.
    """
    raise NotImplementedError


def _strip_processor_prefixes(value: str) -> str:
    """Remove payment rail noise wrapped around the merchant name.

    TODO:
      1. Remove a :data:`PROCESSOR_MARKERS` prefix and any separator after it.
      2. Handle the asterisk form, ``GRAB *TRANSPORT SG``, where the marker is
         attached to what follows.
      3. Leave the merchant name itself untouched.
    """
    raise NotImplementedError


def _collapse_whitespace_and_punctuation(value: str) -> str:
    """Reduce a name to its comparable core.

    Write this one first. It is the cheapest rule and it alone passes several
    corpus cases, which gives you a green test to build the rest against.

    TODO:
      1. Lowercase.
      2. Remove apostrophes so ``McDonald's`` matches ``MCDONALDS``.
      3. Collapse runs of whitespace and separators to a single space.
      4. Strip the result.
    """
    raise NotImplementedError

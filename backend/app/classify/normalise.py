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

#: Chain-format or transaction-type words that trail the merchant name rather
#: than identify it, e.g. ``FAIRPRICE FINEST`` or ``SINGTEL BILL PAYMENT``.
#: Same treatment as :data:`OUTLET_MARKERS`, kept separate because these are
#: descriptor noise rather than a physical location.
SUFFIX_NOISE_MARKERS: Final[tuple[str, ...]] = (
    "FINEST",
    "XPRESS",
    "SERVICE STATION",
    "BILL PAYMENT",
)

#: A parent-brand word that leads the descriptor rather than naming the
#: merchant, e.g. the co-op prefix on ``NTUC FAIRPRICE``.
CHAIN_PREFIX_MARKERS: Final[tuple[str, ...]] = (
    "NTUC",
)

#: Payment-processor noise that appears around a merchant name and is not part
#: of it. Entries carrying their own trailing ``*`` are processors whose
#: convention puts the merchant *after* the marker, e.g. ``SQ *COFFEE SHOP``.
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

#: A trailing country or product domain suffix, e.g. the ``.SG`` in
#: ``AMAZON.SG``.
DOMAIN_SUFFIX: Final[re.Pattern[str]] = re.compile(r"\.[A-Za-z]{2,3}$")

#: A "Ref No." field and everything after it, however it is punctuated or
#: spaced (``Ref No. : 745...``, ``REF NO:745...``, ``ref no  745...``), and
#: the "Singapore" that almost always sits directly in front of it on a
#: local-currency line (``... Singapore Ref No. : 745...``). Both are
#: statement boilerplate -- the settlement country and a reference minted
#: fresh on every single transaction -- never part of any merchant's
#: identity, so they are noise regardless of what precedes them -- unlike
#: :data:`TRAILING_NOISE`, which only recognises a bare trailing number and
#: never this text itself.
REF_NO_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:singapore\s+)?ref\s*no\.?\s*:?\s*\d*\s*$", re.IGNORECASE
)

#: A marker whose statement line prints a fresh per-transaction id right
#: after it, e.g. ``BUS/MRT 765695553 SINGAPORE Ref No. : ...`` -- a new
#: number on every ride. :data:`TRAILING_NOISE` cannot reach this: it is
#: anchored to the end of the string, and this id sits in the middle,
#: followed by more (generic, but real) words. Left unstripped, every ride
#: normalises to a different key and becomes a "new" merchant to the
#: cascade forever. Extend from real data, same as every other marker table
#: here -- a transit statement line is the first observed case.
MID_STRING_ID_MARKERS: Final[tuple[str, ...]] = ("BUS/MRT",)


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
    if not raw or raw.isspace():
        return ""
    value = raw
    value = _strip_processor_prefixes(value)
    value = _strip_mid_string_transaction_ids(value)
    value = _strip_outlet_codes(value)
    value = _collapse_whitespace_and_punctuation(value)
    return value


def strip_reference_suffix(raw: str) -> str:
    """Remove a trailing bank reference field (see :data:`REF_NO_SUFFIX`),
    and nothing else.

    A deliberately narrow cut of :func:`normalise`'s pipeline, for callers
    that need this one piece of boilerplate gone but must not run the rest
    of it -- notably :meth:`~app.services.review.ReviewService.split_descriptor`,
    which exists precisely because normalise() has already folded this raw
    string into the wrong merchant; running normalise() on it there would
    just reproduce the over-merge the split is correcting.
    """
    return REF_NO_SUFFIX.sub("", raw)


def _strip_mid_string_transaction_ids(value: str) -> str:
    """Remove a per-transaction id printed right after a known marker.

    Distinct from both :data:`OUTLET_MARKERS` (a branch name) and
    :data:`TRAILING_NOISE` (a reference number anchored to the very end of
    the string): this id sits in the *middle* of the descriptor, so neither
    existing rule ever reaches it.
    """
    for marker in MID_STRING_ID_MARKERS:
        value = re.sub(rf"^{re.escape(marker)}\s+\d+\b", marker, value)
    return value


def _strip_outlet_codes(value: str) -> str:
    """Remove the branch or mall a transaction happened at.

    TODO:
      1. Strip a bracketed fragment at the end, as in ``MCDONALDS (CCP)``.
      2. Strip an :data:`OUTLET_MARKERS` fragment after a hyphen or space.
      3. Apply :data:`TRAILING_NOISE` for store numbers and references.
      4. Do not strip a leading digit-hyphen, or ``7-ELEVEN`` loses its name.

    Every rule here is anchored to the end of (what remains of) the string, so
    a leading fragment like ``7-`` is never in scope no matter what follows.
    """
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    value = DOMAIN_SUFFIX.sub("", value)
    value = REF_NO_SUFFIX.sub("", value)

    for marker in (*OUTLET_MARKERS, *SUFFIX_NOISE_MARKERS):
        value = re.sub(rf"[\s-]{re.escape(marker)}.*$", "", value)

    value = TRAILING_NOISE.sub("", value)

    for marker in CHAIN_PREFIX_MARKERS:
        value = re.sub(rf"^{re.escape(marker)}[\s-]+", "", value)

    return value


def _strip_processor_prefixes(value: str) -> str:
    """Remove payment rail noise wrapped around the merchant name.

    TODO:
      1. Remove a :data:`PROCESSOR_MARKERS` prefix and any separator after it.
      2. Handle the asterisk form, ``GRAB *TRANSPORT SG``, where the marker is
         attached to what follows.
      3. Leave the merchant name itself untouched.

    A marker's own trailing ``*`` (``SQ *``, ``PAYPAL *``) means that processor
    puts the merchant *after* the asterisk, so stripping the marker is enough.
    Any other value that still carries an asterisk uses the opposite
    convention seen elsewhere on SG statements, ``MERCHANT *description``
    (``GRAB *TRANSPORT SG``): there the merchant is what precedes it, so the
    asterisk and everything after is the noise to drop.
    """
    for marker in PROCESSOR_MARKERS:
        value = re.sub(rf"^{re.escape(marker)}[\s-]*", "", value)

    value = value.split("*", 1)[0].rstrip()

    return value


def _collapse_whitespace_and_punctuation(value: str) -> str:
    """Reduce a name to its comparable core.

    Write this one first. It is the cheapest rule and it alone passes several
    corpus cases, which gives you a green test to build the rest against.

    TODO:
      1. Lowercase.
      2. Remove apostrophes so ``McDonald's`` matches ``MCDONALDS``.
      3. Collapse runs of whitespace and separators to a single space.
      4. Strip the result.

    A hyphen flanked by two alphanumeric characters is kept, not collapsed:
    ``7-ELEVEN`` loses its name to an aggressive rule otherwise. Every other
    hyphen is separator noise, same as any other punctuation.
    """
    value = value.lower()
    value = value.replace("'", "").replace("\u2019", "")
    value = re.sub(r"(?<=\w)-(?=\w)", "\0", value)
    value = re.sub(r"[^\w\0]+", " ", value)
    value = value.replace("\0", "-")
    value = re.sub(r"\s+", " ", value).strip()

    return value

"""The transfer filter. GATE.

Excludes person-to-person transfers before anything else looks at a descriptor.

**The ordering is the entire point.** This runs before the cascade resolver, not
after. Placing it after would satisfy the privacy rule on paper, because no
person would be stored as a merchant, while the name had already been sent to a
third-party model and written into a log line on the way there. The rule is not
"do not store people as merchants"; it is "a person's name does not leave this
system", and only the ordering enforces that.

Concretely, a PayNow transfer to a friend produces a descriptor containing that
friend's name. They never consented to anything. They are not a user. They are
a third party whose name appeared on someone else's bank statement.

===========================================================================
BUILD STEP 0.3   depends on: nothing   blocks: 5.3 cascade
Verify: uv run pytest tests/unit/classify/test_transfer.py
===========================================================================
Pure string work, so it needs nothing else. Build it early: every later step
that touches descriptors assumes this gate already exists in front of it.

See TC-REV-011, TC-REV-012, TC-SEC-007, and ADR-014 for why the same reasoning
puts raw descriptors on the logging deny list.
"""

from __future__ import annotations

from typing import Final

#: Transfer rails seen on Singapore statements. A descriptor carrying one of
#: these is a transfer instruction, and what follows it is very often a person.
TRANSFER_MARKERS: Final[tuple[str, ...]] = (
    "PAYNOW",
    "PAYLAH",
    "FAST TRANSFER",
    "IBG",
    "GIRO TO",
    "TRANSFER TO",
    "FUNDS TRANSFER",
    "I-BANK TFR",
)


def is_person_transfer(descriptor: str) -> bool:
    """True when this row is a transfer to or from an individual.

    Err towards true. The two mistakes are not symmetric:

    * a false positive drops a merchant from the categoriser, and the user sees
      one uncategorised row and can fix it in a click
    * a false negative sends a real person's name to a model provider and into
      a log, and nothing the user can do afterwards undoes that

    TODO:
      1. Return ``False`` for empty input.
      2. Return ``False`` when no :data:`TRANSFER_MARKERS` rail appears. A
         descriptor with no transfer rail is a merchant.
      3. When a rail does appear, take the fragment after it and ask
         :func:`_looks_like_person_name`.
      4. Decide the ambiguous case and make it return ``True``. A bare
         ``PAYNOW TRANSFER 91234567`` has a rail and a phone number rather than
         a name, and PayNow is used by hawker stalls as well as by people.
         Excluding it costs one uncategorised row; not excluding it can leak.
      5. Sanity-check against the merchant list in the test. A filter that
         excludes everything satisfies the privacy rule and destroys the
         product.
    """
    if not descriptor or descriptor.isspace():
        return False

    for marker in TRANSFER_MARKERS:
        if marker in descriptor:
            fragment = descriptor.split(marker, 1)[1].strip()
            return _looks_like_person_name(fragment)

    return False


def _looks_like_person_name(fragment: str) -> bool:
    """Whether a descriptor fragment reads as an individual's name.

    TODO:
      1. Start from shape, not vocabulary: word count, absence of company
         suffixes such as PTE, LTD, LLP, and absence of retail words.
      2. Treat a bare phone number after a transfer rail as a person.
      3. Do not build or import a list of given names. Singapore names may be
         Chinese, Malay, Tamil or English in origin, are frequently three or
         more words, and are usually fully capitalised in exactly the way a shop
         name is, so a name list is both leaky and endless.
      4. Prefer a rule you can explain in one sentence. This function decides
         whether a stranger's name leaves the system, and a rule nobody can
         articulate cannot be reviewed.
    """
    if not fragment or fragment.isspace():
        return False

    words = fragment.split()
    if len(words) < 2:
        return False

    for word in words:
        if word.upper() in {"PTE", "LTD", "LLP"}:
            return False

    return True

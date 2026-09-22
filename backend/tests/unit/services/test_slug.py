"""slugify. GREEN - this is written, not stubbed.

Pinned here because it's now the identity key for two different things
(category slugs and, since the merchant near-duplicate fix, Merchant.name_key)
-- a change to the regex silently changes what counts as "the same" for both.
"""

from __future__ import annotations

from app.services._slug import slugify


def test_casing_and_hyphenation_collapse_to_the_same_slug() -> None:
    assert slugify("Old Tea Hut") == slugify("OLD-TEA-HUT") == "old-tea-hut"


def test_empty_input_falls_back_to_the_default_word() -> None:
    assert slugify("") == "category"
    assert slugify("   ") == "category"


def test_empty_input_uses_a_caller_supplied_fallback() -> None:
    assert slugify("", fallback="merchant") == "merchant"

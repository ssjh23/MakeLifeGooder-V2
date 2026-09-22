"""Deriving a stable machine slug from a display name.

Shared by :class:`~app.services.review.ReviewService` (a category created
inline during classification), :class:`~app.services.account.CategoryService`
(BUILD STEP 9.2, the same operation from the category management screen), and
:class:`~app.db.repositories.MerchantRepository` (a merchant's ``name_key``,
so two canonical names differing only in casing or hyphenation resolve to one
merchant). The slug is what code and rules reference; the display name is
free to change.
"""

from __future__ import annotations

import re


def slugify(name: str, *, fallback: str = "category") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or fallback

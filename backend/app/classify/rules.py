"""Rule pattern matching -- the pure, in-memory half.

``RuleRepository`` (BUILD STEP 8.1) implements the same four match types in
SQL, for counting matches over the whole transaction table cheaply. This is
the one-descriptor-at-a-time version, for a caller that already has a
candidate in hand -- the review board checking a resolved category against a
standing rule for every merchant group on screen -- and would rather not
round-trip to the database once per group to ask.
"""

from __future__ import annotations

from app.db.models import MatchType, Rule


def matches(
    pattern: str,
    match_type: MatchType,
    descriptor_key: str,
    *,
    ignore_case: bool = True,
    match_negative: bool = False,
) -> bool:
    haystack = descriptor_key.lower() if ignore_case else descriptor_key
    needle = pattern.lower() if ignore_case else pattern

    if match_type == MatchType.CONTAINS:
        result = needle in haystack
    elif match_type == MatchType.STARTS_WITH:
        result = haystack.startswith(needle)
    elif match_type == MatchType.ENDS_WITH:
        result = haystack.endswith(needle)
    else:
        result = haystack == needle

    return not result if match_negative else result


def matches_row(rule: Rule, *, descriptor_key: str | None, description_raw: str) -> bool:
    """A rule matches a row if its pattern matches either descriptor_key or
    description_raw. Mirrors ``RuleRepository._condition()``'s SQL structure
    exactly, so the in-memory and database halves of rule matching agree:
    ``match_negative`` is applied once to the OR of both checks, not to each
    independently (the De Morgan dual of "matches neither").
    """
    positive = matches(
        rule.pattern,
        rule.match_type,
        descriptor_key or "",
        ignore_case=rule.ignore_case,
        match_negative=False,
    ) or matches(
        rule.pattern,
        rule.match_type,
        description_raw,
        ignore_case=rule.ignore_case,
        match_negative=False,
    )
    return not positive if rule.match_negative else positive

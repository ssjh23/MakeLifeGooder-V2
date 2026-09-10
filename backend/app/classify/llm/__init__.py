from __future__ import annotations

from typing import TYPE_CHECKING

from app.classify.llm.base import LLMAdapter, LLMUnavailable, MerchantSuggestion
from app.classify.llm.fake import FailingLLMAdapter, FakeLLMAdapter

if TYPE_CHECKING:
    from app.config import Settings


def build_llm_adapter(settings: Settings) -> LLMAdapter:
    """Select the adapter.

    The fake is the default in every environment except production, so nothing
    reaches a paid provider by accident and no test depends on one being
    reachable.
    """
    if settings.llm_adapter == "claude":
        from app.classify.llm.claude import ClaudeAdapter

        return ClaudeAdapter(settings)
    return FakeLLMAdapter()


__all__ = [
    "FailingLLMAdapter",
    "FakeLLMAdapter",
    "LLMAdapter",
    "LLMUnavailable",
    "MerchantSuggestion",
    "build_llm_adapter",
]

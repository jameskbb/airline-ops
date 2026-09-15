"""Brief providers.

The deterministic provider is always available. An LLM provider can be enabled
with ``FLIGHTOPS_BRIEF_PROVIDER=anthropic`` plus an Anthropic credential
(``ANTHROPIC_API_KEY`` env var or Streamlit secret). The LLM only receives the
structured facts dict; it never sees raw data and never computes numbers. Its
output is rejected, and the deterministic brief used instead, if it contains a
figure that does not appear in the facts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

from flightops.narrative.deterministic import Statement, deterministic_brief

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """You write the "Latest Operations Brief" for an airline operations executive.
You receive a JSON object of pre-computed facts about U.S. airline operations from the U.S. DOT
Bureau of Transportation Statistics (BTS). Write 3 to 5 concise sentences, one per line, with no
bullets or numbering. Prioritize what an operations leader most needs to know.

Rules:
- Use only numbers that appear in the facts, formatted as given (percentages, "pts", minutes).
  Never calculate, round differently, or estimate new figures.
- Describe delay causes as "reported" causes and use "associated with" rather than causal claims.
- The data is historical monthly reporting, not real time; do not say "today".
"""


class BriefProvider(Protocol):
    name: str

    def generate(self, facts: dict) -> list[Statement]: ...


class DeterministicBriefProvider:
    name = "Deterministic rules"

    def generate(self, facts: dict) -> list[Statement]:
        return deterministic_brief(facts)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _allowed_numbers(facts: dict, fallback: list[Statement]) -> set[str]:
    corpus = json.dumps(facts) + " ".join(s.text for s in fallback)
    numbers = set(_NUMBER.findall(corpus))
    for key in ("on_time_rate", "cancellation_rate", "severe_delay_rate"):
        value = (facts.get("network") or {}).get(key)
        if isinstance(value, float):
            numbers.add(f"{value * 100:.1f}")
    return numbers


def numbers_are_grounded(text: str, allowed: set[str]) -> bool:
    return all(n in allowed or n.replace(",", "") in allowed for n in _NUMBER.findall(text))


class AnthropicBriefProvider:
    """Narrates facts with Claude. Falls back to deterministic output on any failure."""

    name = "Claude (facts-grounded)"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic  # optional dependency: pip install ".[llm]"

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model or os.environ.get("FLIGHTOPS_LLM_MODEL", DEFAULT_MODEL)

    def generate(self, facts: dict) -> list[Statement]:
        import anthropic

        fallback = deterministic_brief(facts)
        # The deterministic statements are included as pre-formatted candidate phrasings.
        payload = {"facts": facts, "candidate_statements": [s.text for s in fallback]}
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            return fallback
        if response.stop_reason == "refusal":
            return fallback
        text = "\n".join(block.text for block in response.content if block.type == "text")
        lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
        allowed = _allowed_numbers(facts, fallback)
        if not 3 <= len(lines) <= 5 or not all(numbers_are_grounded(ln, allowed) for ln in lines):
            return fallback
        return [Statement(ln, "neutral", 0, "llm") for ln in lines]


def get_provider(secrets: dict | None = None) -> BriefProvider:
    """Resolve the configured provider; any misconfiguration degrades to deterministic."""
    secrets = secrets or {}
    choice = (os.environ.get("FLIGHTOPS_BRIEF_PROVIDER") or secrets.get("FLIGHTOPS_BRIEF_PROVIDER") or "").lower()
    if choice != "anthropic":
        return DeterministicBriefProvider()
    key = os.environ.get("ANTHROPIC_API_KEY") or secrets.get("ANTHROPIC_API_KEY")
    try:
        return AnthropicBriefProvider(api_key=key)
    except Exception:  # optional dependency missing or client misconfigured
        return DeterministicBriefProvider()

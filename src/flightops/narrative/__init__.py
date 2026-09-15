"""Narrative generation from structured facts (deterministic by default)."""

from flightops.narrative.deterministic import Statement, deterministic_brief
from flightops.narrative.providers import BriefProvider, get_provider

__all__ = ["BriefProvider", "Statement", "deterministic_brief", "get_provider"]

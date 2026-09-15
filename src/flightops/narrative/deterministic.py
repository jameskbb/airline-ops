"""Deterministic Latest Operations Brief.

Rule-based statements rendered from :class:`BriefFacts`. Each candidate statement
carries a materiality score; the brief keeps the 3–5 most material ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from flightops.utils.format import fmt_pct


@dataclass(frozen=True)
class Statement:
    text: str
    tone: str  # "positive" | "negative" | "neutral"
    materiality: float
    topic: str


def _tone(favorable: bool | None) -> str:
    return "neutral" if favorable is None else ("positive" if favorable else "negative")


def deterministic_brief(facts: dict, max_items: int = 5) -> list[Statement]:
    out: list[Statement] = []
    net = facts.get("network") or {}
    comps = facts.get("comparisons") or {}
    scope = facts.get("scope", "the network")

    otp = net.get("on_time_rate")
    otp_cmp = comps.get("on_time_rate", {})
    if otp is not None:
        prior = otp_cmp.get("vs_prior")
        yoy = otp_cmp.get("vs_prior_year")
        if prior:
            verb = "improved" if prior["change"] > 0 else "fell" if prior["change"] < 0 else "held flat"
            text = f"On-time arrival {verb} to {fmt_pct(otp)} for {scope}, {prior['text']} versus the prior period"
            text += f" and {yoy['text']} year over year." if yoy else "."
            out.append(Statement(text, _tone(prior["favorable"]), 50 + abs(prior["change"]) * 5, "on_time"))
        else:
            out.append(Statement(f"On-time arrival was {fmt_pct(otp)} for {scope}.", "neutral", 40, "on_time"))

    canc = comps.get("cancellation_rate", {}).get("vs_prior")
    if canc and abs(canc["change"]) >= 0.3:
        direction = "rose" if canc["change"] > 0 else "fell"
        out.append(Statement(
            f"Cancellations {direction} to {fmt_pct(net.get('cancellation_rate'))} of scheduled flights "
            f"({canc['text']} vs prior period).",
            _tone(canc["favorable"]), 30 + abs(canc["change"]) * 15, "cancellations",
        ))

    hotspot = facts.get("hotspot")
    if hotspot:
        out.append(Statement(
            f"{hotspot['airport']} accounted for {fmt_pct(hotspot['delay_share'])} of reported delay minutes "
            f"despite representing {fmt_pct(hotspot['flight_share'])} of analyzed departures.",
            "negative", 35 + (hotspot["delay_share"] - hotspot["flight_share"]) * 400, "hotspot",
        ))

    cause = facts.get("largest_cause")
    if cause:
        out.append(Statement(
            f"{cause['label']} delays were the largest reported delay category, at "
            f"{fmt_pct(cause['share'])} of delay minutes.",
            "neutral", 30 + cause["share"] * 10, "cause",
        ))

    mover = facts.get("carrier_mover")
    if mover and abs(mover["change_pts_vs_trailing_3m"]) >= 1.0:
        change = mover["change_pts_vs_trailing_3m"]
        verb = "improved" if change > 0 else "declined"
        out.append(Statement(
            f"{mover['name']} {verb} on-time performance {abs(change):.1f} pts versus its trailing three-month "
            f"average, to {fmt_pct(mover['on_time_rate'])}.",
            "positive" if change > 0 else "negative", 25 + abs(change) * 4, "carrier",
        ))

    signal = facts.get("top_signal")
    if signal and signal.get("severity") in ("High impact", "Elevated"):
        out.append(Statement(signal["detail"], "negative" if "deterioration" in signal.get("headline", "") else "neutral",
                             28, "signal"))

    out.sort(key=lambda s: s.materiality, reverse=True)
    # Keep the on-time headline first: it frames every other statement.
    out.sort(key=lambda s: s.topic != "on_time")
    return out[:max_items]

"""Reusable presentation components (KPI cards, headers, signal cards, badges).

Components render small, escaped HTML fragments styled by one stylesheet. Every
comparison shows its sign and an arrow in addition to color.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

import streamlit as st

from flightops.data.months import format_month, parse_month
from flightops.metrics import METRICS, Delta, compare
from flightops.metrics.periods import Period
from flightops.ui import data

CSS = """
<style>
:root{--fo-surface:#121821;--fo-surface2:#18202b;--fo-border:#222b36;--fo-ink:#e6e9ee;--fo-ink2:#aab3bf;
--fo-muted:#7d8794;--fo-good:#3fb950;--fo-bad:#f0625a;--fo-warn:#e0a526;--fo-accent:#4c93ea;}
.block-container{padding-top:1.4rem;padding-bottom:2.5rem;max-width:1500px;}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebarNav"] a span{font-size:13.5px;}
h1,h2,h3{letter-spacing:-0.01em;}
.fo-brand{display:flex;flex-direction:column;gap:2px;padding:2px 0 10px;border-bottom:1px solid var(--fo-border);margin-bottom:6px}
.fo-brand-name{font-weight:700;font-size:16px;color:var(--fo-ink);letter-spacing:-0.01em}
.fo-brand-name span{color:var(--fo-accent)}
.fo-brand-sub{font-size:11.5px;color:var(--fo-muted)}
.fo-side-label{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--fo-muted);font-weight:600;margin:10px 0 2px}
.fo-header{display:flex;justify-content:space-between;align-items:flex-end;gap:12px 20px;flex-wrap:wrap;
  border-bottom:1px solid var(--fo-border);padding-bottom:12px;margin-bottom:12px}
.fo-eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--fo-accent);font-weight:600}
.fo-title{font-size:25px;font-weight:650;color:var(--fo-ink);margin:2px 0 0;line-height:1.2}
.fo-sub{color:var(--fo-ink2);font-size:13.5px;margin-top:4px;max-width:880px}
.fo-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.fo-badge{border:1px solid var(--fo-border);border-radius:999px;padding:3px 10px;font-size:11.5px;color:var(--fo-ink2);
  background:#0f141b;white-space:nowrap}
.fo-badge .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--fo-accent);margin-right:6px;vertical-align:1px}
.fo-chips{display:flex;flex-wrap:wrap;gap:6px}
.fo-chip{font-size:11.5px;color:var(--fo-ink2);background:var(--fo-surface);border:1px solid var(--fo-border);border-radius:5px;padding:2px 8px}
.fo-chip b{color:var(--fo-ink);font-weight:600}
.fo-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:10px;margin:2px 0 14px}
.fo-kpi{background:var(--fo-surface);border:1px solid var(--fo-border);border-radius:8px;padding:11px 14px 10px;min-width:0}
.fo-kpi{cursor:help}
.fo-kpi-label{font-size:10.5px;color:var(--fo-muted);text-transform:uppercase;letter-spacing:.06em;font-weight:600;
  display:flex;justify-content:space-between;align-items:flex-start;gap:6px;line-height:1.3;min-height:1.3em}
.fo-kpi-label i{font-style:normal;flex:none;width:13px;height:13px;border:1px solid #3a4552;border-radius:50%;
  font-size:9px;line-height:11px;text-align:center;color:#6b7684;text-transform:none;font-family:Georgia,serif}
.fo-kpi-value{font-size:25px;font-weight:650;color:var(--fo-ink);margin:3px 0 5px;letter-spacing:-0.01em;white-space:nowrap}
.fo-kpi-note{font-size:11.5px;color:var(--fo-muted)}
.fo-delta{font-size:11.5px;color:var(--fo-muted);line-height:1.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fo-delta b{font-weight:600}
.fo-good{color:var(--fo-good)}.fo-bad{color:var(--fo-bad)}.fo-flat{color:var(--fo-ink2)}
.fo-section{margin:6px 0 6px}
.fo-section h3{font-size:15px;font-weight:600;color:var(--fo-ink);margin:0;padding:0}
.fo-section p{font-size:12.5px;color:var(--fo-muted);margin:2px 0 0;line-height:1.45}
.fo-brief{list-style:none;margin:0;padding:0}
.fo-brief li{padding:9px 0 9px 16px;border-bottom:1px solid var(--fo-border);position:relative;font-size:13.5px;color:var(--fo-ink);line-height:1.5}
.fo-brief li:last-child{border-bottom:none}
.fo-brief li:before{content:"";position:absolute;left:0;top:16px;width:6px;height:6px;border-radius:50%;background:var(--fo-ink2)}
.fo-brief li.negative:before{background:var(--fo-bad)}.fo-brief li.positive:before{background:var(--fo-good)}
.fo-brief li.neutral:before{background:var(--fo-accent)}
.fo-signal{background:var(--fo-surface);border:1px solid var(--fo-border);border-left:3px solid var(--fo-ink2);border-radius:8px;padding:12px 14px;margin-bottom:10px}
.fo-signal.sev-high{border-left-color:var(--fo-bad)}.fo-signal.sev-elev{border-left-color:var(--fo-warn)}.fo-signal.sev-watch{border-left-color:#56606c}
.fo-signal.improvement{border-left-color:var(--fo-good)}
.fo-signal-top{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}
.fo-signal-sev{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;font-weight:700}
.fo-signal-title{font-size:14.5px;font-weight:600;color:var(--fo-ink);margin:3px 0 4px}
.fo-signal-detail{font-size:13px;color:var(--fo-ink2);line-height:1.5}
.fo-signal-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:6px 16px;margin-top:9px;
  padding-top:8px;border-top:1px solid var(--fo-border)}
.fo-signal-grid div{font-size:11.5px;color:var(--fo-muted)}.fo-signal-grid b{display:block;color:var(--fo-ink);font-weight:600;font-size:12.5px}
.fo-empty{border:1px dashed var(--fo-border);border-radius:8px;padding:22px;text-align:center;color:var(--fo-muted);font-size:13px}
.fo-empty b{display:block;color:var(--fo-ink2);font-size:14px;margin-bottom:4px}
.fo-footer{margin-top:28px;padding-top:10px;border-top:1px solid var(--fo-border);font-size:11.5px;color:var(--fo-muted)}
.fo-note{font-size:12px;color:var(--fo-muted);margin:-2px 0 6px}
.fo-lineage{display:flex;flex-direction:column;gap:0;align-items:stretch;max-width:520px}
.fo-lineage div.step{background:var(--fo-surface);border:1px solid var(--fo-border);border-radius:6px;padding:8px 12px;font-size:13px;color:var(--fo-ink)}
.fo-lineage div.step small{display:block;color:var(--fo-muted);font-size:11.5px}
.fo-lineage div.arrow{height:16px;margin-left:22px;border-left:1px solid #3a4552}
[data-testid="stMetricValue"]{font-size:22px}
div[data-testid="stVerticalBlockBorderWrapper"]{border-color:var(--fo-border)!important;background:rgba(18,24,33,.55)}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value))


def freshness_text() -> str:
    meta = data.metadata()
    end = meta.get("coverage_end")
    return f"Data through {format_month(parse_month(end))}" if end else "No data loaded"


def page_header(eyebrow: str, title: str, subtitle: str, filters=None, show_chips: bool = True) -> None:
    chips = ""
    if filters is not None and show_chips:
        chips = '<div class="fo-chips">' + "".join(
            f'<span class="fo-chip">{esc(k)} <b>{esc(v)}</b></span>' for k, v in filters.chips
        ) + "</div>"
    st.markdown(
        f"""<div class="fo-header">
          <div><div class="fo-eyebrow">{esc(eyebrow)}</div><div class="fo-title">{esc(title)}</div>
          <div class="fo-sub">{esc(subtitle)}</div></div>
          <div class="fo-meta"><span class="fo-badge" title="BTS publishes monthly with a reporting lag; this is not real-time data">
          <span class="dot"></span>{esc(freshness_text())} · monthly BTS reporting</span>{chips}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def section(title: str, caption: str | None = None) -> None:
    cap = f"<p>{esc(caption)}</p>" if caption else ""
    st.markdown(f'<div class="fo-section"><h3>{esc(title)}</h3>{cap}</div>', unsafe_allow_html=True)


def delta_html(delta: Delta | None, suffix: str) -> str:
    if delta is None:
        return f'<div class="fo-delta">— {esc(suffix)}</div>'
    if delta.favorable is None:
        cls, arrow = "fo-flat", "▲" if delta.change > 0 else "▼" if delta.change < 0 else "■"
        if abs(delta.change) < 1e-9:
            arrow = "■"
    else:
        cls = "fo-good" if delta.favorable else "fo-bad"
        arrow = "▲" if delta.change > 0 else "▼"
    return f'<div class="fo-delta"><b class="{cls}">{arrow} {esc(delta.text)}</b> {esc(suffix)}</div>'


def kpi_card(metric_key: str, value, comparisons: Iterable[tuple[Delta | None, str]] = (),
             label: str | None = None, note: str | None = None) -> str:
    metric = METRICS[metric_key]
    formatted = metric.format(value, compact=metric_key in ("delay_minutes",) or (value or 0) >= 10_000_000)
    deltas = "".join(delta_html(d, s) for d, s in comparisons)
    note_html = f'<div class="fo-kpi-note">{esc(note)}</div>' if note else ""
    return (
        f'<div class="fo-kpi" title="{esc(metric.definition)}">'
        f'<div class="fo-kpi-label"><span>{esc(label or metric.label)}</span><i>i</i></div>'
        f'<div class="fo-kpi-value">{esc(formatted)}</div>{deltas}{note_html}</div>'
    )


def text_card(label: str, value: str, note: str = "", tooltip: str = "") -> str:
    return (
        f'<div class="fo-kpi" title="{esc(tooltip)}"><div class="fo-kpi-label"><span>{esc(label)}</span></div>'
        f'<div class="fo-kpi-value">{esc(value)}</div><div class="fo-kpi-note">{esc(note)}</div></div>'
    )


def kpi_grid(cards: list[str]) -> None:
    st.markdown('<div class="fo-kpis">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def compare_cards(keys: Iterable[str], period: Period, current: dict, previous: dict, prior_year: dict,
                  labels: dict[str, str] | None = None) -> list[str]:
    """KPI cards with prior-period and YoY comparisons for each metric key."""
    labels = labels or {}
    prev_label = "vs prior month" if period.months == 1 else f"vs prior {period.months} mo"
    cards = []
    for key in keys:
        metric = METRICS[key]
        comps = [
            (compare(metric, current.get(key), previous.get(key)) if previous else None, prev_label),
            (compare(metric, current.get(key), prior_year.get(key)) if prior_year else None, "YoY"),
        ]
        cards.append(kpi_card(key, current.get(key), comps, label=labels.get(key)))
    return cards


def brief(statements, provider_name: str) -> None:
    items = "".join(f'<li class="{esc(s.tone)}">{esc(s.text)}</li>' for s in statements)
    st.markdown(f'<ul class="fo-brief">{items}</ul>', unsafe_allow_html=True)
    st.caption(f"Generated by: {provider_name}. Every figure comes from the metric layer; statements are selected by materiality.")


SEVERITY_CLASS = {"High impact": "sev-high", "Elevated": "sev-elev", "Watch": "sev-watch"}
SEVERITY_COLOR = {"High impact": "#f0625a", "Elevated": "#e0a526", "Watch": "#8b95a1"}


def signal_card(signal) -> str:
    cls = SEVERITY_CLASS[signal.severity] + (" improvement" if signal.direction == "improvement" else "")
    metric = METRICS[signal.metric]
    if signal.kind == "delay_concentration":
        cur, base = f"{signal.current * 100:.1f}% of delay min", f"{signal.baseline * 100:.1f}% of departures"
    elif signal.kind == "cause_shift":
        cur, base = f"{signal.current * 100:.1f}% share", f"{signal.baseline * 100:.1f}% share"
    else:
        cur, base = metric.format(signal.current), metric.format(signal.baseline)
    direction = {"deterioration": "Deterioration", "improvement": "Improvement", "shift": "Mix shift"}[signal.direction]
    color = "#3fb950" if signal.direction == "improvement" else SEVERITY_COLOR[signal.severity]
    return f"""<div class="fo-signal {cls}">
      <div class="fo-signal-top"><span class="fo-signal-sev" style="color:{color}">{esc(signal.severity)} · {esc(direction)}</span>
      <span class="fo-chip">{esc(signal.category)}</span></div>
      <div class="fo-signal-title">{esc(signal.headline)}</div>
      <div class="fo-signal-detail">{esc(signal.detail)}</div>
      <div class="fo-signal-grid">
        <div>Current<b>{esc(cur)}</b></div><div>Baseline<b>{esc(base)}</b></div>
        <div>Estimated impact<b>{esc(signal.extras.get("impact_text", "—"))}</b></div>
        <div>Comparison<b>{esc(signal.comparison)}</b></div>
      </div>
      <div class="fo-signal-detail" style="margin-top:8px"><span style="color:#7d8794">Why it matters · </span>{esc(signal.why)}</div>
    </div>"""


def empty_state(title: str, message: str) -> None:
    st.markdown(f'<div class="fo-empty"><b>{esc(title)}</b>{esc(message)}</div>', unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f'<div class="fo-note">{esc(text)}</div>', unsafe_allow_html=True)


def footer() -> None:
    meta = data.metadata()
    st.markdown(
        f'<div class="fo-footer">Source: U.S. DOT Bureau of Transportation Statistics, Marketing Carrier On-Time '
        f'Performance | Updated through {esc(meta.get("coverage_end", "—"))} | Historical monthly reporting, '
        f"not real-time flight status.</div>",
        unsafe_allow_html=True,
    )

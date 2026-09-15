"""Visual design tokens and the Plotly template shared by every chart.

Categorical colors are a validated, CVD-safe set (dark-surface steps), assigned to
entities in a fixed order so a carrier or delay cause keeps its color everywhere.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# Surfaces & ink
PAGE = "#0b0f14"
SURFACE = "#121821"
SURFACE_2 = "#18202b"
BORDER = "#222b36"
GRID = "#1f2833"
AXIS = "#39434f"
INK = "#e6e9ee"
INK_2 = "#aab3bf"
MUTED = "#7d8794"
ACCENT = "#4c93ea"

# Status (reserved for good/bad meaning; always paired with a sign or label)
GOOD = "#2fb344"
BAD = "#e5534b"
WARN = "#e0a526"

CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER = "#5b6572"

CARRIER_ORDER = ["AA", "DL", "UA", "WN", "AS", "B6", "F9", "G4"]
CAUSE_ORDER = ["late_aircraft", "carrier", "nas", "weather", "security"]
CAUSE_COLORS = {
    "late_aircraft": CATEGORICAL[0],
    "carrier": CATEGORICAL[1],
    "nas": CATEGORICAL[2],
    "weather": CATEGORICAL[3],
    "security": CATEGORICAL[4],
}

# Diverging (better ↔ worse than reference): blue ↔ red through neutral gray.
DIVERGING = [
    [0.0, "#2a78d6"], [0.25, "#5f93cf"], [0.5, "#3a3f47"], [0.75, "#c86a5f"], [1.0, "#e5534b"],
]
# Sequential for "friction" magnitude on a dark surface: dark → bright warm.
SEQUENTIAL = [
    [0.0, "#17202b"], [0.2, "#3b2f2a"], [0.45, "#7a4125"], [0.7, "#c2571f"], [0.88, "#ec7a33"], [1.0, "#ffb26b"],
]


def carrier_color(code: str) -> str:
    return CATEGORICAL[CARRIER_ORDER.index(code)] if code in CARRIER_ORDER else OTHER


FONT = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"


def _template() -> go.layout.Template:
    axis = dict(
        gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickcolor=AXIS, ticks="",
        tickfont=dict(color=MUTED, size=11), title=dict(font=dict(color=INK_2, size=12)),
        showline=False, automargin=True,
    )
    return go.layout.Template(
        layout=go.Layout(
            font=dict(family=FONT, color=INK_2, size=12),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            colorway=CATEGORICAL,
            margin=dict(l=8, r=12, t=8, b=8),
            xaxis=dict(axis, showgrid=False),
            yaxis=dict(axis, showgrid=True),
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                        font=dict(color=INK_2, size=11), bgcolor="rgba(0,0,0,0)", title=None),
            hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=BORDER, font=dict(family=FONT, color=INK, size=12),
                            align="left"),
            hovermode="closest",
            bargap=0.28,
            coloraxis=dict(colorbar=dict(outlinewidth=0, thickness=10, tickfont=dict(color=MUTED, size=10))),
        )
    )


pio.templates["flightops"] = _template()
pio.templates.default = "flightops"

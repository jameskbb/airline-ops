"""PyDeck network map: airports as bubbles, routes as arcs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pydeck as pdk

BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"

_BETTER = np.array([57, 135, 229])  # blue
_NEUTRAL = np.array([92, 100, 112])  # gray
_WORSE = np.array([240, 98, 90])  # red


def diverging_rgb(score: float) -> list[int]:
    """Map a score in [-1, 1] (negative = better, positive = worse) to RGB via a gray midpoint."""
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return [*_NEUTRAL.tolist()]
    s = float(np.clip(score, -1, 1))
    end = _WORSE if s > 0 else _BETTER
    rgb = _NEUTRAL + (end - _NEUTRAL) * abs(s)
    return [int(v) for v in rgb]


def sequential_rgb(score: float) -> list[int]:
    """0 → dim warm, 1 → bright orange (magnitude-only metrics such as total delay minutes)."""
    s = float(np.clip(0 if score is None or np.isnan(score) else score, 0, 1))
    low, high = np.array([90, 70, 60]), np.array([255, 150, 70])
    return [int(v) for v in low + (high - low) * s]


def network_deck(airports: pd.DataFrame, arcs: pd.DataFrame | None, tooltip_html: str,
                 focus: tuple[float, float] | None = None) -> pdk.Deck:
    """``airports`` needs lon, lat, radius, color, plus tooltip fields; ``arcs`` needs
    src/dst coordinates, color and width."""
    layers = []
    if arcs is not None and not arcs.empty:
        layers.append(pdk.Layer(
            "ArcLayer", data=arcs, id="routes", get_source_position=["src_lon", "src_lat"],
            get_target_position=["dst_lon", "dst_lat"], get_source_color="color", get_target_color="color",
            get_width="width", width_min_pixels=1, width_max_pixels=8, great_circle=True, pickable=True,
            auto_highlight=True,
        ))
    layers.append(pdk.Layer(
        "ScatterplotLayer", data=airports, id="airports", get_position=["longitude", "latitude"],
        get_radius="radius", get_fill_color="color", get_line_color=[18, 24, 33], line_width_min_pixels=1,
        stroked=True, radius_min_pixels=3, radius_max_pixels=30, opacity=0.85, pickable=True, auto_highlight=True,
    ))
    view = pdk.ViewState(latitude=focus[1] if focus else 38.5, longitude=focus[0] if focus else -96.5,
                         zoom=3.55 if not focus else 3.9, pitch=28, bearing=0)
    return pdk.Deck(
        layers=layers, initial_view_state=view, map_style=BASEMAP,
        tooltip={"html": tooltip_html, "style": {
            "backgroundColor": "#18202b", "color": "#e6e9ee", "fontFamily": "Inter, sans-serif",
            "fontSize": "12px", "border": "1px solid #2b3645", "borderRadius": "6px", "padding": "8px 10px"}},
    )

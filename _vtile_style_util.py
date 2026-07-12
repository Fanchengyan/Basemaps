# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""Vector tile style normalization shared by the style cache and the preview.

Providers occasionally expose a *TileJSON* metadata document where QGIS expects
a *Mapbox GL style* (the ``Basemaps`` UI lets users paste any ``style_url``).
Loading a TileJSON as a Mapbox style yields an empty renderer, so this module
detects the document shape and — when it is a TileJSON — synthesises a generic
Mapbox v8 style from its ``vector_layers``/``tiles`` fields.

When the document is already a Mapbox style, the existing relative-URL rewrite
behavior (``sprite``, ``glyphs``, ``sources[*].url``) is preserved.

The functions are extracted verbatim from ``preview_manager.py`` so the preview
pipeline and the real layer-loading pipeline share one implementation.
"""

from __future__ import annotations

import json
from urllib.parse import urljoin

from .messageTool import Logger


def looks_like_mapbox_style(payload: dict) -> bool:
    """Return whether a JSON payload resembles a Mapbox style."""
    return bool(
        payload.get("version") and payload.get("layers") and payload.get("sources")
    )


def looks_like_tilejson(payload: dict) -> bool:
    """Return whether a JSON payload resembles TileJSON metadata."""
    return bool(payload.get("tiles"))


def layer_palette(source_layer_name: str, index: int) -> tuple[str, str]:
    """Generate deterministic fill/line colors for a source layer."""
    palettes = [
        ("#6f9ceb", "#315fba"),
        ("#83b992", "#2f7d4f"),
        ("#c9a35b", "#8c5c1e"),
        ("#b989c5", "#7d3f9c"),
        ("#6db6b2", "#246f79"),
        ("#d07b7b", "#993232"),
    ]
    palette_index = (sum(ord(char) for char in source_layer_name) + index) % len(
        palettes
    )
    return palettes[palette_index]


def build_generic_vector_style(
    tilejson_payload: dict, layer_name: str
) -> dict | None:
    """Build a generic Mapbox v8 style from TileJSON metadata.

    Emits one ``fill`` + ``line`` + ``circle`` style layer per entry in
    ``vector_layers``, using :func:`layer_palette` for deterministic colors.
    Returns ``None`` when the TileJSON is missing ``tiles`` or
    ``vector_layers``.
    """
    tiles = tilejson_payload.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        return None

    vector_layers = tilejson_payload.get("vector_layers", [])
    if not isinstance(vector_layers, list) or not vector_layers:
        Logger.warning(
            f"TileJSON metadata for {layer_name} does not include vector_layers"
        )
        return None

    source_name = "preview_source"
    style_layers: list[dict] = []
    for index, source_layer in enumerate(vector_layers):
        source_layer_name = source_layer.get("id")
        if not source_layer_name:
            continue
        fill_color, line_color = layer_palette(source_layer_name, index)
        style_layers.append(
            {
                "id": f"{source_layer_name}_fill",
                "type": "fill",
                "source": source_name,
                "source-layer": source_layer_name,
                "paint": {
                    "fill-color": fill_color,
                    "fill-opacity": 0.55,
                },
            }
        )
        style_layers.append(
            {
                "id": f"{source_layer_name}_line",
                "type": "line",
                "source": source_name,
                "source-layer": source_layer_name,
                "paint": {
                    "line-color": line_color,
                    "line-width": 1.1,
                    "line-opacity": 0.9,
                },
            }
        )
        style_layers.append(
            {
                "id": f"{source_layer_name}_circle",
                "type": "circle",
                "source": source_name,
                "source-layer": source_layer_name,
                "paint": {
                    "circle-color": line_color,
                    "circle-radius": 2.5,
                    "circle-opacity": 0.95,
                },
            }
        )

    if not style_layers:
        return None

    return {
        "version": 8,
        "name": f"{layer_name} preview",
        "sources": {
            source_name: {
                "type": "vector",
                "tiles": tiles,
                "minzoom": tilejson_payload.get("minzoom", 0),
                "maxzoom": tilejson_payload.get("maxzoom", 14),
            }
        },
        "layers": style_layers,
    }


def _rewrite_mapbox_urls(data: dict, base_url: str) -> str:
    """Rewrite relative URLs in a Mapbox style dict to absolute.

    Rewrites ``sprite``, ``glyphs``, and every ``sources[*].url`` that
    contains a relative path.  ``{fontstack}``, ``{range}`` and similar
    template placeholders are preserved — only the path portion is
    joined with *base_url*.

    Returns the re-serialized JSON text, or the original text unchanged
    when the JSON cannot be parsed.
    """
    changed = False
    for field in ("sprite", "glyphs"):
        value = data.get(field)
        if isinstance(value, str) and not value.startswith(
            ("http://", "https://", "file://")
        ):
            data[field] = urljoin(base_url, value)
            changed = True

    sources = data.get("sources")
    if isinstance(sources, dict):
        for src in sources.values():
            if not isinstance(src, dict):
                continue
            url = src.get("url")
            if isinstance(url, str) and not url.startswith(
                ("http://", "https://", "file://")
            ):
                src["url"] = urljoin(base_url, url)
                changed = True

    if not changed:
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            return json.dumps(data) if data else ""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return json.dumps(data)


def normalize_style_text(
    text: str, base_url: str, layer_name: str = ""
) -> str:
    """Normalize a downloaded style/TileJSON document for local caching.

    Dispatch by JSON shape:

    * **TileJSON** (``tiles`` key present, no Mapbox ``layers``/``sources``)
      → synthesize a generic Mapbox v8 style via
      :func:`build_generic_vector_style`; fall back to the original text
      when synthesis is not possible.
    * **Mapbox style** (``version`` + ``layers`` + ``sources``)
      → rewrite relative ``sprite``/``glyphs``/``sources[*].url`` to
      absolute URLs against *base_url* (existing behavior, ported from
      ``StyleCache._rewrite_relative_urls``).
    * **parse failure / unknown shape** → return the original text
      unchanged so callers can still persist it as a best-effort cache.
    """
    if not text:
        return text
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text
    if not isinstance(data, dict):
        return text

    # TileJSON takes precedence: a TileJSON has ``tiles`` but no Mapbox
    # ``layers``/``sources``. A Mapbox style never carries a top-level
    # ``tiles`` array.
    if looks_like_tilejson(data) and not looks_like_mapbox_style(data):
        generic = build_generic_vector_style(data, layer_name)
        if generic is not None:
            try:
                return json.dumps(generic, ensure_ascii=False, indent=2)
            except (ValueError, TypeError):
                return json.dumps(generic)
        Logger.info(
            f"TileJSON for {layer_name or base_url} lacked vector_layers; saving raw"
        )
        return text

    if looks_like_mapbox_style(data):
        return _rewrite_mapbox_urls(data, base_url)

    return text
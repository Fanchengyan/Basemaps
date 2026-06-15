# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""QGIS Browser panel integration for the Basemaps plugin.

Adds a top-level ``Basemaps`` node to the QGIS Browser panel that mirrors
the provider/layer catalog of the main plugin window. The tree is built
lazily: each level only populates when the user expands it, so the 1500+
basemaps catalog never blocks QGIS startup or browsing.

Hierarchy::

    Basemaps                       (BasemapsRootItem)
    ├── XYZ / Vector Tiles         (GroupCollectionItem, type=xyz)
    │   └── Provider               (ProviderCollectionItem)
    │       └── Layer              (BasemapLayerItem)
    └── WMS / WMTS                 (GroupCollectionItem, type=wms)
        └── Provider               (ProviderCollectionItem)
            └── Layer              (WmsLayerItem)

The leaf items implement :meth:`handleDoubleClick` and :meth:`mimeUri` so
double-click and drag-and-drop both load the layer via
:mod:`layer_loader`. No network requests or capabilities fetches happen
during population — everything works from the on-disk YAML catalog.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDataCollectionItem,
    QgsDataItem,
    QgsDataSourceUri,
    QgsMimeDataUtils,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

from . import config_loader
from . import layer_loader

# Qt5/Qt6 + QGIS enum-scope compatibility. The BrowserItemType and
# BrowserItemState enums were moved into the Qgis scope in QGIS 3.30+.
try:
    _LAYER_TYPE = Qgis.BrowserItemType.Layer  # QGIS 3.30+
except AttributeError:  # pragma: no cover - QGIS < 3.30
    _LAYER_TYPE = QgsDataItem.Layer

try:
    _STATE_POPULATED = Qgis.BrowserItemState.Populated  # QGIS 3.30+
except AttributeError:  # pragma: no cover - QGIS < 3.30
    _STATE_POPULATED = QgsDataItem.Populated

# QGIS raster/vector-tile layer type codes for mime URIs.
try:
    _BROWSER_RASTER = Qgis.BrowserLayerType.Raster
    _BROWSER_VTILE = Qgis.BrowserLayerType.VectorTile
except AttributeError:  # pragma: no cover - very old QGIS
    _BROWSER_RASTER = QgsDataItem.Layer
    _BROWSER_VTILE = QgsDataItem.Layer

# Path to the plugin's resources directory (used for provider icons).
_RESOURCES_DIR = Path(__file__).parent / "resources"
_ICONS_DIR = _RESOURCES_DIR / "icons"
_PREVIEWS_DIR = _RESOURCES_DIR / "previews"

# Display names for the two top-level groups. These strings are translated
# via QCoreApplication using the "BasemapsBrowser" context.
_XYZ_GROUP_KEY = "xyz"
_WMS_GROUP_KEY = "wms"

# Sort order for basemap/layer items within each provider, matching the
# main dialog's TAG_SORT_ORDER so the Browser panel displays layers in
# the same sequence.
TAG_SORT_ORDER = [
    "Satellite",
    "Streets",
    "Terrain",
    "Thematic",
    "Overlay/Labels",
    "Overlay/Boundaries",
    "Overlay/Transportation",
    "Overlay/Hydrography",
    "Overlay",
]


def _sort_key_by_tag(item: dict) -> int:
    """Return sort key for a basemap/layer based on its tags.

    Items are ordered by the first matching tag in TAG_SORT_ORDER.
    Items without a recognized tag are placed at the end.
    """
    if not isinstance(item, dict):
        return len(TAG_SORT_ORDER)
    tags = item.get("tags", [])
    if not tags:
        return len(TAG_SORT_ORDER)
    for tag in tags:
        if tag in TAG_SORT_ORDER:
            return TAG_SORT_ORDER.index(tag)
    return len(TAG_SORT_ORDER)


def _tr(message: str) -> str:
    """Translate a string in the BasemapsBrowser context."""
    return QCoreApplication.translate("BasemapsBrowser", message)


def _provider_icon(icon_value: str) -> QIcon:
    """Resolve a provider ``icon`` value to a QIcon.

    The value may be either an absolute/relative filesystem path or a
    path relative to the plugin resources directory (e.g.
    ``icons/foo.svg``). Falls back to the default folder icon.
    """
    if icon_value:
        candidate = Path(icon_value)
        if not candidate.is_absolute():
            candidate = _ICONS_DIR / icon_value
        if candidate.exists():
            return QIcon(str(candidate))
    return QIcon()


def _preview_path(provider: dict[str, Any], layer_name: str) -> Path | None:
    """Return the cached preview image path, or None if it does not exist."""
    source = provider.get("source_file", "")
    is_default = "/providers/default/" in source.replace("\\", "/")
    prefix = "default" if is_default else "user"

    service_type = provider.get("type", "xyz")
    if service_type == "wms":
        st = provider.get("service_type", "wms")
        subdir = "wms" if st in ("wms", "wmts") else "xyz"
    else:
        subdir = "xyz"

    safe_provider = "".join(c for c in provider.get("name", "") if c.isalnum())
    safe_layer = "".join(c for c in layer_name if c.isalnum())
    path = _PREVIEWS_DIR / prefix / subdir / f"{safe_provider}_{safe_layer}.png"
    return path if path.exists() else None


def _load_catalog() -> list[dict[str, Any]]:
    """Load and merge default + user providers, applying tag overrides.

    Returns the flat provider list exactly as the main dialog uses it, so
    the Browser never maintains a second copy of the catalog.
    """
    providers: list[dict[str, Any]] = []
    for prefix in ("default", "user"):
        try:
            providers.extend(
                config_loader.load_all_provider_files(_RESOURCES_DIR, prefix)
            )
        except Exception:
            # Loading must never crash the Browser; logged by config_loader.
            continue

    try:
        overrides = config_loader.load_tag_overrides(_RESOURCES_DIR)
        config_loader.apply_tag_overrides(providers, overrides)
    except Exception:
        pass

    return providers


# ---------------------------------------------------------------------------
# Root + group items
# ---------------------------------------------------------------------------


class BasemapsRootItem(QgsDataCollectionItem):
    """Top-level ``Basemaps`` node shown in the Browser panel."""

    def __init__(self, icon: QIcon | None = None) -> None:
        super().__init__(None, "Basemaps", "/Basemaps")
        if icon is not None:
            self.setIcon(icon)
        self.setSortKey(0)

    def createChildren(self):
        children = [
            GroupCollectionItem(self, _XYZ_GROUP_KEY),
            GroupCollectionItem(self, _WMS_GROUP_KEY),
        ]
        from qgis.PyQt import sip

        for idx, child in enumerate(children):
            child.setSortKey(idx)
            sip.transferto(child, self)
        return children


class GroupCollectionItem(QgsDataCollectionItem):
    """One of the two category nodes: XYZ/Vector Tiles or WMS/WMTS.

    Children (provider nodes) are built lazily in :meth:`createChildren`,
    which only runs when the user expands this group.
    """

    def __init__(self, parent: QgsDataItem, group_key: str) -> None:
        if group_key == _XYZ_GROUP_KEY:
            label = _tr("XYZ / Vector Tiles")
        else:
            label = _tr("WMS / WMTS")
        super().__init__(parent, label, f"/Basemaps/{group_key}")
        self._group_key = group_key

    def createChildren(self):
        from qgis.PyQt import sip

        children = []
        for idx, provider in enumerate(_load_catalog()):
            if provider.get("type") != self._group_key:
                continue
            # Skip separators / unknown entries defensively.
            if provider.get("type") == "separator":
                continue
            if not provider.get("name"):
                continue

            item = ProviderCollectionItem(self, provider)
            item.setSortKey(idx)
            sip.transferto(item, self)
            children.append(item)
        return children


# ---------------------------------------------------------------------------
# Provider items
# ---------------------------------------------------------------------------


class ProviderCollectionItem(QgsDataCollectionItem):
    """A provider node. Its children (layers) are built lazily on expand."""

    def __init__(self, parent: QgsDataItem, provider: dict[str, Any]) -> None:
        name = provider.get("name", "provider")
        super().__init__(parent, name, f"/Basemaps/{provider.get('type')}/{name}")
        self._provider = provider
        self.setIcon(_provider_icon(provider.get("icon", "")))

    def createChildren(self):
        from qgis.PyQt import sip

        children = []
        provider_type = self._provider.get("type")
        if provider_type == _XYZ_GROUP_KEY:
            items = sorted(
                self._provider.get("basemaps", []),
                key=_sort_key_by_tag,
            )
            for idx, basemap in enumerate(items):
                if not basemap.get("name"):
                    continue
                item = BasemapLayerItem(self, self._provider, basemap)
                item.setSortKey(idx)
                sip.transferto(item, self)
                children.append(item)
        elif provider_type == _WMS_GROUP_KEY:
            items = sorted(
                self._provider.get("layers", []),
                key=_sort_key_by_tag,
            )
            for idx, layer_data in enumerate(items):
                title = layer_data.get("layer_title") or layer_data.get(
                    "layer_name"
                )
                if not title:
                    continue
                item = WmsLayerItem(self, self._provider, layer_data)
                item.setSortKey(idx)
                sip.transferto(item, self)
                children.append(item)
        return children


# ---------------------------------------------------------------------------
# Leaf (layer) items
# ---------------------------------------------------------------------------


def _mime_uri(
    name: str, provider_key: str, layer_type: str, uri: str
) -> QgsMimeDataUtils.Uri:
    """Build a QgsMimeDataUtils.Uri for drag-and-drop support.

    ``provider_key`` is the QGIS data provider ("wms" for raster XYZ/WMS/WMTS,
    "xyz" for vector tiles). ``layer_type`` matches the string codes used by
    QGIS mime handling ("raster" or "vector-tile").

    Note: for vector tile layers, native drag-drop loads the tile without its
    remote style (the background style download only runs via double-click).
    The dragged layer is still a valid, loadable vector tile layer.
    """
    mime = QgsMimeDataUtils.Uri()
    mime.name = name
    mime.providerKey = provider_key
    mime.layerType = layer_type
    mime.uri = uri
    # Set the supportedLayer enum where the attribute exists, so QGIS
    # recognizes the dragged item as a loadable layer type.
    if hasattr(mime, "supportedLayer"):
        try:
            if layer_type == "raster":
                mime.supportedLayer = _BROWSER_RASTER
            elif layer_type == "vector-tile":
                mime.supportedLayer = _BROWSER_VTILE
        except (AttributeError, TypeError):
            pass
    return mime


class BasemapLayerItem(QgsDataItem):
    """A single XYZ raster or vector tile layer leaf node.

    Loads the layer via :mod:`layer_loader` on double-click or drag-and-drop.
    No preview thumbnails are fetched here — that stays in the main window.
    """

    def __init__(
        self,
        parent: QgsDataItem,
        provider: dict[str, Any],
        basemap: dict[str, Any],
    ) -> None:
        super().__init__(
            _LAYER_TYPE,
            parent,
            basemap.get("name", "basemap"),
            f"/Basemaps/xyz/{provider.get('name')}/{basemap.get('name')}",
        )
        # Leaf node: mark as populated so QGIS does not look for children.
        self.setState(_STATE_POPULATED)
        self._provider = provider
        self._basemap = basemap

        tile_type = basemap.get("tile_type", "raster")
        tags = " · ".join(basemap.get("tags", []))
        type_label = _tr("Vector Tile") if tile_type == "vector" else _tr("XYZ Tile")
        if tile_type == "vector":
            self.setIcon(QgsApplication.getThemeIcon("mIconVectorTileLayer.svg"))
        else:
            self.setIcon(QgsApplication.getThemeIcon("mIconXyz.svg"))
        tip = f'<table width="140"><tr><td>{tags}</td><td align="right">{type_label}</td></tr></table>'
        preview = _preview_path(provider, basemap.get("name", ""))
        if preview:
            tip = f'<img src="{preview}" width="140" height="100"><br>{tip}'
        self.setToolTip(tip)

    # ---- interaction ------------------------------------------------------

    def handleDoubleClick(self):
        layer_loader.load_xyz_basemap(self._provider, self._basemap)
        return True

    def _build_mime_uri(self) -> QgsMimeDataUtils.Uri:
        tile_type = self._basemap.get("tile_type", "raster")
        token = self._provider.get("token", "")
        token_param = self._provider.get(
            "token_param", layer_loader.DEFAULT_TOKEN_PARAM
        )
        url = layer_loader.append_token(
            self._basemap.get("url", ""), token, token_param
        )
        # Build the QGIS datasource URI the same way the loader does.
        uri = QgsDataSourceUri()
        uri.setParam("type", "xyz")
        uri.setParam("url", url)
        encoded = str(uri.encodedUri(), "utf-8")

        if tile_type == "vector":
            return _mime_uri(self.name(), "xyz", "vector-tile", encoded)
        return _mime_uri(self.name(), "wms", "raster", "type=xyz&" + encoded)

    def mimeUri(self):
        # Deprecated since 3.18 but still consulted by some code paths.
        return self._build_mime_uri()

    def mimeUris(self):
        # Preferred API since QGIS 3.18.
        return [self._build_mime_uri()]

    def hasDragEnabled(self):
        return True


class WmsLayerItem(QgsDataItem):
    """A single WMS/WMTS layer leaf node."""

    def __init__(
        self,
        parent: QgsDataItem,
        provider: dict[str, Any],
        layer_data: dict[str, Any],
    ) -> None:
        title = layer_data.get("layer_title") or layer_data.get(
            "layer_name", "layer"
        )
        super().__init__(
            _LAYER_TYPE,
            parent,
            title,
            f"/Basemaps/wms/{provider.get('name')}/{title}",
        )
        self.setState(_STATE_POPULATED)
        self._provider = provider
        self._layer_data = layer_data
        self.setIcon(QgsApplication.getThemeIcon("mIconRaster.svg"))

        tags = " · ".join(layer_data.get("tags", []))
        service_type = layer_data.get(
            "service_type", provider.get("service_type", "wms")
        )
        tip = f'<table width="140"><tr><td>{tags}</td><td align="right">{service_type.upper()}</td></tr></table>'
        preview = _preview_path(provider, title)
        if preview:
            tip = f'<img src="{preview}" width="140" height="100"><br>{tip}'
        self.setToolTip(tip)

    def handleDoubleClick(self):
        layer_loader.load_wms_layer(self._provider, self._layer_data)
        return True

    def _build_mime_uri(self) -> QgsMimeDataUtils.Uri:
        service_type = self._layer_data.get(
            "service_type", self._provider.get("service_type", "wms")
        )
        token = self._provider.get("token", "")
        token_param = self._provider.get(
            "token_param", layer_loader.DEFAULT_TOKEN_PARAM
        )
        url = layer_loader.append_token(
            self._provider.get("url", "").strip(), token, token_param
        )

        uri = QgsDataSourceUri()
        if service_type == "wmts":
            uri.setParam("url", url)
            uri.setParam("layers", self._layer_data.get("layer_name", ""))
            if self._layer_data.get("crs"):
                uri.setParam("tileMatrixSet", self._layer_data["crs"][0])
            if self._layer_data.get("format"):
                uri.setParam("format", self._layer_data["format"][0])
            uri.setParam(
                "styles",
                self._layer_data["styles"][0]
                if self._layer_data.get("styles")
                else "",
            )
        else:
            uri.setParam("url", url)
            uri.setParam("layers", self._layer_data.get("layer_name", ""))
            uri.setParam(
                "format", (self._layer_data.get("format") or ["image/png"])[0]
            )
            uri.setParam("crs", (self._layer_data.get("crs") or ["EPSG:3857"])[0])
            uri.setParam(
                "styles",
                self._layer_data["styles"][0]
                if self._layer_data.get("styles")
                else "",
            )

        encoded = str(uri.encodedUri(), "utf-8")
        return _mime_uri(self.name(), "wms", "raster", encoded)

    def mimeUri(self):
        return self._build_mime_uri()

    def mimeUris(self):
        return [self._build_mime_uri()]

    def hasDragEnabled(self):
        return True

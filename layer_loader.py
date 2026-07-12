# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""Shared basemap loading helpers.

These functions build QGIS layers from the provider/basemap dictionaries
loaded by :mod:`config_loader`. They are deliberately free of any UI
dependency so they can be reused by both the main Basemaps dialog and the
QGIS Browser panel integration.

The logic mirrors the dialog's load paths (``BasemapsDialog.load_xyz_basemap``,
``_load_wms_layer``, ``_load_wmts_layer``) but reports failures through
:class:`messageTool.MessageBar` instead of modal message boxes, which suits
the lightweight Browser entry point.

Token handling follows the same convention as the dialog: when a provider
declares a ``token`` field but it is empty, loading is aborted with a
warning so the user knows to configure the provider first.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsBlockingNetworkRequest,
    QgsDataSourceUri,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
    QgsVectorTileLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from .messageTool import Logger, MessageBar, MessageBox
from .style_cache import get_style_cache, safe_file_url

# Qt5/Qt6 compatibility for the HTTP-status attribute enum scope.
try:  # Qt6 / QGIS 3.28+
    _HTTP_STATUS_ATTRIBUTE = QNetworkRequest.Attribute.HttpStatusCodeAttribute
except AttributeError:  # Qt5
    _HTTP_STATUS_ATTRIBUTE = QNetworkRequest.HttpStatusCodeAttribute

# Headers used when fetching a remote Mapbox/TileJSON vector style.
_VECTOR_STYLE_REQUEST_HEADERS = (
    (
        b"User-Agent",
        b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        b"AppleWebKit/537.36 (KHTML, like Gecko) "
        b"Chrome/120.0.0.0 Safari/537.36",
    ),
    (b"Accept", b"application/json, text/plain, */*"),
)

TOKEN_PARAM_OPTIONS = ["apikey", "key", "api_key", "access_token", "token", "tk"]
DEFAULT_TOKEN_PARAM = TOKEN_PARAM_OPTIONS[0]

# Callback invoked when a token is missing and the user opts to set it.
# Signature: callback(provider_name: str, provider_type: str) -> None
_token_missing_callback = None


def set_token_missing_callback(callback) -> None:
    """Register a callback invoked when a token is missing and user opts to set it."""
    global _token_missing_callback
    _token_missing_callback = callback


def append_token(url: str, token: str, token_param: str = DEFAULT_TOKEN_PARAM) -> str:
    """Append an API token as a query parameter to *url*.

    A thin wrapper kept here so the Browser layer loader is self-contained
    and does not depend on the dialog module.
    """
    if not url or not token:
        return url
    param_name = token_param.strip() or DEFAULT_TOKEN_PARAM
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({param_name: token})}"


def _provider_is_missing_token(provider: dict[str, Any]) -> bool:
    """Return True when a provider declares ``token`` but leaves it empty."""
    return "token" in provider and not provider.get("token", "").strip()


def _ask_token_missing(
    provider_name: str, provider_type: str
) -> None:
    """Ask the user whether to set the missing token; open edit dialog if yes.

    Parameters
    ----------
    provider_name : str
        Provider name shown in the prompt and used to locate the edit dialog.
    provider_type : str
        ``"xyz"`` or ``"wms"``.
    """
    result = MessageBox.question(
        QCoreApplication.translate(
            "BasemapsBrowser",
            "Provider '{}' requires an API token. Set it now?",
        ).format(provider_name),
        QCoreApplication.translate("BasemapsBrowser", "Authentication Required"),
    )
    if result == MessageBox.YES and _token_missing_callback:
        _token_missing_callback(provider_name, provider_type)


def _report_load_failure(name: str, detail: str = "") -> None:
    """Report a layer load failure through the message bar."""
    text = QCoreApplication.translate(
        "BasemapsBrowser", "Failed to load basemap: '{}'"
    ).format(name)
    if detail:
        text = f"{text}\n{detail}"
    MessageBar.show(
        QCoreApplication.translate("BasemapsBrowser", "Basemaps"),
        text,
        level=Qgis.MessageLevel.Critical,
        duration=10,
    )


# ---------------------------------------------------------------------------
# XYZ raster + vector tile loading
# ---------------------------------------------------------------------------


def load_xyz_basemap(provider: dict[str, Any], basemap: dict[str, Any]) -> None:
    """Load a single XYZ basemap (raster or vector tile) into the project.

    Parameters
    ----------
    provider : dict
        Provider dictionary; supplies ``token`` / ``token_param``.
    basemap : dict
        Basemap dictionary with ``name``, ``url``, optional ``tile_type``
        and ``style_url``.
    """
    token = provider.get("token", "")
    token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)

    if _provider_is_missing_token(provider):
        _ask_token_missing(provider.get("name", ""), "xyz")
        return

    name = basemap.get("name", "basemap")
    tile_type = basemap.get("tile_type", "raster")

    try:
        if tile_type == "vector":
            _load_vector_tile(provider, basemap, token, token_param, name)
        elif tile_type == "group":
            _load_group_tile(provider, basemap, token, token_param, name)
        else:
            _load_xyz_raster(basemap, token, token_param, name)
    except (KeyError, TypeError) as exc:
        _report_load_failure(name, str(exc))


def _load_xyz_raster(
    basemap: dict[str, Any],
    token: str,
    token_param: str,
    name: str,
) -> None:
    """Create and add a raster XYZ layer."""
    url = append_token(basemap["url"], token, token_param)
    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam("url", url)
    layer = QgsRasterLayer(str(uri.encodedUri(), "utf-8"), name, "wms")
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
    else:
        error = layer.error().message() if layer.error() else ""
        Logger.critical(f"Failed to load basemap '{name}': {error}")
        _report_load_failure(name, error)


def _load_vector_tile(
    provider: dict[str, Any],
    basemap: dict[str, Any],
    token: str,
    token_param: str,
    name: str,
) -> None:
    """Create and add a vector tile layer, downloading its style in the background."""
    source_url = append_token(basemap.get("url", ""), token, token_param)
    style_url = append_token(basemap.get("style_url", ""), token, token_param)

    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    if source_url:
        uri.setParam("url", source_url)
    encoded_uri = str(uri.encodedUri(), "utf-8")

    # Determine default/user status from provider source file.
    source_file = provider.get("source_file", "")
    is_default = "default" in source_file if source_file else True

    task = _VectorTileLoadTask(encoded_uri, name, style_url, provider.get("name", ""), basemap.get("name", ""), is_default)
    # Keep a reference on the task manager's app-level registry to avoid GC.
    # The task removes itself from this list when it finishes.
    _VectorTileLoadTask._active_tasks.append(task)
    task.taskCompleted.connect(lambda t=task: _VectorTileLoadTask._drop(t))
    task.taskTerminated.connect(lambda t=task: _VectorTileLoadTask._drop(t))
    QgsApplication.taskManager().addTask(task)


def _fetch_group_style(style_url: str) -> tuple[str | None, str | None]:
    """Synchronously fetch a group style JSON; return (text, etag).

    A simplified non-QgsTask version of
    :meth:`_VectorTileLoadTask._fetch_style` for group basemaps, which
    need the style.json available before any child layer can be created.
    """
    request = QNetworkRequest(QUrl(style_url))
    for header, value in _VECTOR_STYLE_REQUEST_HEADERS:
        request.setRawHeader(header, value)

    network_request = QgsBlockingNetworkRequest()
    error_code = network_request.get(request, True)
    if error_code != QgsBlockingNetworkRequest.NoError:
        Logger.warning(
            QCoreApplication.translate(
                "BasemapsBrowser",
                "Failed to download group style '{}': {}",
            ).format(style_url, network_request.errorMessage())
        )
        return None, None

    reply = network_request.reply()
    status_code = reply.attribute(_HTTP_STATUS_ATTRIBUTE)
    if status_code and int(status_code) >= 400:
        Logger.warning(
            QCoreApplication.translate(
                "BasemapsBrowser",
                "Failed to download group style '{}': HTTP {}",
            ).format(style_url, status_code)
        )
        return None, None

    # Extract ETag
    raw_etag = reply.rawHeader(b"ETag")
    etag: str | None = None
    if raw_etag:
        try:
            etag = bytes(raw_etag).decode("utf-8").strip()
        except (UnicodeDecodeError, TypeError):
            etag = None
        if etag and etag.startswith('"') and etag.endswith('"') and len(etag) >= 2:
            etag = etag[1:-1]

    content = bytes(reply.content())
    if not content:
        return None, None
    try:
        return content.decode("utf-8"), etag
    except UnicodeDecodeError:
        return None, None


def _load_group_tile(
    provider: dict[str, Any],
    basemap: dict[str, Any],
    token: str,
    token_param: str,
    name: str,
) -> None:
    """Schedule async creation of a group (multi-source) basemap.

    A group basemap has a ``sources`` list — each entry references one
    tileset that the shared ``style_url`` (a Mapbox style document) uses as a
    ``sources`` key.  QGIS's :class:`QgsVectorTileLayer` URI accepts only one
    tile ``url``; the style.json is applied for rendering and QGIS draws only
    the style layers whose ``source`` field matches the layer's tile URL.

    For each source we create one layer (vector or raster) and add it to a
    :class:`QgsLayerTreeGroup` so all sources render together as one basemap.

    The style.json download runs in a background :class:`QgsTask` to avoid
    blocking the UI; layer/group creation happens on the main thread in
    :meth:`_GroupLoadTask.finished`.
    """
    style_url = append_token(basemap.get("style_url", ""), token, token_param)
    sources = basemap.get("sources", [])
    # Need at least sources; style_url is optional if every vector source
    # carries its own style_url.
    has_per_source_styles = any(
        s.get("style_url", "").strip() for s in sources
        if s.get("source_type", "vector") == "vector"
    )
    if not sources or (not style_url and not has_per_source_styles):
        Logger.warning(
            QCoreApplication.translate(
                "BasemapsBrowser",
                "Group basemap '{}' is missing sources or style_url",
            ).format(name)
        )
        return

    source_file = provider.get("source_file", "")
    is_default = "default" in source_file if source_file else True

    # Prepend tokens to each source URL so the background task doesn't need
    # to touch provider/basemap dicts again.
    prepared_sources = []
    for src in sources:
        prepared_sources.append({
            "url": append_token(src.get("url", ""), token, token_param),
            "source_name": src.get("source_name", ""),
            "source_type": src.get("source_type", "vector"),
            # Per-source style_url (optional) overrides the group style.
            "style_url": append_token(src.get("style_url", ""), token, token_param),
        })

    task = _GroupLoadTask(
        name,
        style_url,
        provider.get("name", ""),
        is_default,
        prepared_sources,
    )
    _GroupLoadTask._active_tasks.append(task)
    task.taskCompleted.connect(lambda t=task: _GroupLoadTask._drop(t))
    task.taskTerminated.connect(lambda t=task: _GroupLoadTask._drop(t))
    QgsApplication.taskManager().addTask(task)


def _insert_group_above_selection(root, name: str):
    """Insert a new layer group above the currently selected layer.

    Falls back to appending at the end when nothing is selected or the
    selection cannot be located (same behaviour as ``root.addGroup``).
    """
    try:
        from qgis.utils import iface as _iface
    except ImportError:
        return root.addGroup(name)

    view = _iface.layerTreeView() if _iface else None
    if view is None:
        return root.addGroup(name)

    selected = view.selectedNodes()
    if not selected:
        return root.addGroup(name)

    node = selected[0]
    parent = node.parent() if node.parent() else root
    try:
        index = parent.children().index(node)
    except (ValueError, AttributeError):
        return root.addGroup(name)

    return parent.insertGroup(index, name)


class _GroupLoadTask(QgsTask):
    """Background task for loading a group (multi-source) basemap.

    Downloads the shared style.json in ``run()`` (background thread), then
    creates a :class:`QgsLayerTreeGroup` with one layer per source in
    ``finished()`` (main thread).
    """

    _active_tasks: list["_GroupLoadTask"] = []

    @classmethod
    def _drop(cls, task: "_GroupLoadTask") -> None:
        if task in cls._active_tasks:
            cls._active_tasks.remove(task)

    def __init__(
        self,
        name: str,
        style_url: str,
        provider_name: str,
        is_default: bool,
        sources: list[dict[str, str]],
    ) -> None:
        super().__init__(
            QCoreApplication.translate(
                "BasemapsBrowser", "Loading group basemap..."
            ),
            QgsTask.Flag.CanCancel,
        )
        self.name = name
        self.style_url = style_url
        self.provider_name = provider_name
        self.is_default = is_default
        self.sources = sources
        self.temp_style_path: str | None = None
        # Per-source cached style paths, keyed by source index.  A source
        # may carry its own ``style_url`` that overrides the group style.
        self.per_source_style_paths: dict[int, str] = {}

    def run(self) -> bool:
        """Download style JSONs (background thread). Always returns True."""
        cache = get_style_cache()

        # Group style (shared fallback).
        if self.style_url:
            cached = cache.get_cached_style(
                self.provider_name, self.name, self.is_default
            )
            if cached:
                self.temp_style_path = str(cached)
            else:
                style_text, etag = _fetch_group_style(self.style_url)
                if style_text:
                    cache.save(
                        self.provider_name, self.name, self.is_default,
                        style_text, etag or "", style_url=self.style_url,
                    )
                    cached = cache.get_cached_style(
                        self.provider_name, self.name, self.is_default
                    )
                    if cached:
                        self.temp_style_path = str(cached)

        # Per-source styles (override the group style when present).
        for idx, src in enumerate(self.sources):
            src_style_url = src.get("style_url", "")
            if not src_style_url:
                continue
            src_name = src.get("source_name", "") or f"{self.name}_{idx}"
            cached = cache.get_cached_style(
                self.provider_name, f"{self.name}__{src_name}", self.is_default
            )
            if cached:
                self.per_source_style_paths[idx] = str(cached)
                continue
            style_text, etag = _fetch_group_style(src_style_url)
            if style_text:
                cache.save(
                    self.provider_name,
                    f"{self.name}__{src_name}",
                    self.is_default,
                    style_text, etag or "", style_url=src_style_url,
                )
                cached = cache.get_cached_style(
                    self.provider_name, f"{self.name}__{src_name}", self.is_default
                )
                if cached:
                    self.per_source_style_paths[idx] = str(cached)
        return True

    def finished(self, result: bool) -> None:
        """Create the layer group and child layers on the main thread."""
        if not self.temp_style_path and not self.per_source_style_paths:
            Logger.critical(
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Failed to download group style: '{}'",
                ).format(self.name)
            )
            _report_load_failure(self.name)
            return

        cached_style_url = safe_file_url(self.temp_style_path) if self.temp_style_path else ""

        # Create the layer group, inserting above the currently selected
        # layer so it behaves like a normal layer addition (selected layers
        # and everything below shift down by one).
        root = QgsProject.instance().layerTreeRoot()
        group = _insert_group_above_selection(root, self.name)

        for idx, src in enumerate(self.sources):
            src_url = src.get("url", "")
            src_name = src.get("source_name", "")
            src_type = src.get("source_type", "vector")
            layer_display_name = src_name or self.name

            if not src_url:
                Logger.warning(
                    QCoreApplication.translate(
                        "BasemapsBrowser",
                        "Composite source '{}' has no url; skipped",
                    ).format(src_name or "?")
                )
                continue

            if src_type == "raster":
                # Raster / raster-dem source: load as plain RGB raster via the
                # wms (xyz) provider. QGIS has no built-in terrain-rgb decoder,
                # so raster-dem tiles render as coloured RGB, not hillshade.
                uri = QgsDataSourceUri()
                uri.setParam("type", "xyz")
                uri.setParam("url", src_url)
                layer = QgsRasterLayer(
                    str(uri.encodedUri(), "utf-8"), layer_display_name, "wms"
                )
            else:
                # Vector source: prefer a per-source style if one was
                # downloaded; otherwise fall back to the shared group style.
                src_style_path = self.per_source_style_paths.get(idx)
                if src_style_path:
                    style_url_for_layer = safe_file_url(src_style_path)
                else:
                    style_url_for_layer = cached_style_url
                uri = QgsDataSourceUri()
                uri.setParam("type", "xyz")
                uri.setParam("url", src_url)
                encoded = str(uri.encodedUri(), "utf-8")
                if style_url_for_layer:
                    encoded += f"&styleUrl={style_url_for_layer}"
                layer = QgsVectorTileLayer(encoded, layer_display_name)
                if layer.isValid():
                    layer.loadDefaultStyle()

            if layer.isValid():
                # Register in the project without adding to the root tree,
                # then move into the group.
                QgsProject.instance().addMapLayer(layer, False)
                group.addLayer(layer)
            else:
                error = layer.error().message() if layer.error() else ""
                Logger.warning(
                    QCoreApplication.translate(
                        "BasemapsBrowser",
                        "Failed to load group source '{}': {}",
                    ).format(layer_display_name, error)
                )


class _VectorTileLoadTask(QgsTask):
    """Background task that downloads a vector tile style then adds the layer.

    This is a compact sibling of ``BasemapsDialog.VectorTileLoadTask`` kept
    inside this module so the Browser integration stays independent of the
    dialog module (and its heavy UI imports).

    Uses :class:`StyleCache` so the first download is persisted locally and
    subsequent loads read from the cache immediately.  A background ETag
    conditional request validates freshness after the layer is added.
    """

    # Module-level keep-alive list; cleared as tasks finish.
    _active_tasks: list["_VectorTileLoadTask"] = []
    # Keep-alive for background validation tasks spawned after layer load.
    _validation_tasks: list["_StyleCacheValidationTask"] = []

    @classmethod
    def _drop(cls, task: "_VectorTileLoadTask") -> None:
        if task in cls._active_tasks:
            cls._active_tasks.remove(task)

    @classmethod
    def _drop_validation(cls, task: "_StyleCacheValidationTask") -> None:
        if task in cls._validation_tasks:
            cls._validation_tasks.remove(task)

    def __init__(
        self,
        encoded_uri: str,
        name: str,
        style_url: str,
        provider_name: str = "",
        basemap_name: str = "",
        is_default: bool = True,
    ) -> None:
        super().__init__(
            QCoreApplication.translate(
                "BasemapsBrowser", "Loading vector tile basemap..."
            ),
            QgsTask.Flag.CanCancel,
        )
        self.encoded_uri = encoded_uri
        self.name = name
        self.style_url = style_url
        self.provider_name = provider_name
        self.basemap_name = basemap_name
        self.is_default = is_default
        self.temp_style_path: str | None = None
        self._needs_background_validation = False

    def run(self) -> bool:
        """Download the style JSON (best effort). Always returns True."""
        if not self.style_url:
            return True

        cache = get_style_cache()
        cached = cache.get_cached_style(
            self.provider_name, self.basemap_name, self.is_default
        )

        if cached:
            # Use cached file immediately; validate freshness in background.
            self.temp_style_path = str(cached)
            self._needs_background_validation = True
            return True

        # First download — fetch and persist to cache.
        style_text, etag = self._fetch_style()
        if style_text:
            cache.save(
                self.provider_name,
                self.basemap_name,
                self.is_default,
                style_text,
                etag or "",
                style_url=self.style_url,
            )
            cached = cache.get_cached_style(
                self.provider_name, self.basemap_name, self.is_default
            )
            if cached:
                self.temp_style_path = str(cached)
        return True

    def _fetch_style(self) -> tuple[str | None, str | None]:
        """Fetch the style JSON body; return (text, etag)."""
        request = QNetworkRequest(QUrl(self.style_url))
        for header, value in _VECTOR_STYLE_REQUEST_HEADERS:
            request.setRawHeader(header, value)

        network_request = QgsBlockingNetworkRequest()
        error_code = network_request.get(request, True)
        if error_code != QgsBlockingNetworkRequest.NoError:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Failed to download vector tile style '{}': {}",
                ).format(self.style_url, network_request.errorMessage())
            )
            return None, None

        reply = network_request.reply()
        status_code = reply.attribute(_HTTP_STATUS_ATTRIBUTE)
        if status_code and int(status_code) >= 400:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Failed to download vector tile style '{}': HTTP {}",
                ).format(self.style_url, status_code)
            )
            return None, None

        etag = self._extract_etag(reply)
        content = bytes(reply.content())
        if not content:
            return None, None
        try:
            return content.decode("utf-8"), etag
        except UnicodeDecodeError:
            return None, None

    @staticmethod
    def _extract_etag(reply) -> str | None:
        """Extract and clean the ETag header value."""
        raw = reply.rawHeader(b"ETag")
        if not raw:
            return None
        try:
            value = bytes(raw).decode("utf-8").strip()
        except (UnicodeDecodeError, TypeError):
            return None
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        return value or None

    def finished(self, result: bool) -> None:
        """Build the vector tile layer on the main thread."""
        uri = self.encoded_uri
        if self.temp_style_path:
            uri += f"&styleUrl={safe_file_url(self.temp_style_path)}"
        elif self.style_url:
            uri += f"&styleUrl={self.style_url}"

        layer = QgsVectorTileLayer(uri, self.name)
        if layer.isValid():
            if self.temp_style_path:
                layer.loadDefaultStyle()
            QgsProject.instance().addMapLayer(layer)
        else:
            Logger.critical(f"Failed to load vector tile layer: {self.name}")
            _report_load_failure(self.name)

        # Background freshness check (ETag conditional request).
        if (
            self._needs_background_validation
            and self.provider_name
            and self.basemap_name
            and self.style_url
        ):
            vtask = _StyleCacheValidationTask(
                self.provider_name,
                self.basemap_name,
                self.is_default,
                self.style_url,
            )
            _VectorTileLoadTask._validation_tasks.append(vtask)
            vtask.taskCompleted.connect(
                lambda t=vtask: _VectorTileLoadTask._drop_validation(t)
            )
            vtask.taskTerminated.connect(
                lambda t=vtask: _VectorTileLoadTask._drop_validation(t)
            )
            QgsApplication.taskManager().addTask(vtask)


class _StyleCacheValidationTask(QgsTask):
    """Background ETag conditional GET to refresh a cached style.

    Runs silently; shows a message bar notification only when new content
    is downloaded.
    """

    def __init__(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool,
        style_url: str,
    ) -> None:
        super().__init__(
            QCoreApplication.translate(
                "BasemapsBrowser", "Validating vector tile style cache..."
            ),
            QgsTask.Flag.CanCancel,
        )
        self._provider_name = provider_name
        self._basemap_name = basemap_name
        self._is_default = is_default
        self._style_url = style_url
        self._updated = False

    def run(self) -> bool:
        cache = get_style_cache()
        self._updated = cache.validate_cache(
            self._provider_name,
            self._basemap_name,
            self._is_default,
            self._style_url,
        )
        return True

    def finished(self, result: bool) -> None:
        if self._updated:
            MessageBar.show(
                QCoreApplication.translate("BasemapsBrowser", "Basemaps"),
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Vector tile style updated: '{}'",
                ).format(self._basemap_name),
                level=Qgis.MessageLevel.Info,
                duration=8,
            )


# ---------------------------------------------------------------------------
# WMS / WMTS loading
# ---------------------------------------------------------------------------


def load_wms_layer(provider: dict[str, Any], layer_data: dict[str, Any]) -> None:
    """Load a WMS layer into the project.

    Parameters
    ----------
    provider : dict
        Provider dictionary; supplies ``url``, ``token`` and ``service_type``.
    layer_data : dict
        Layer dictionary with ``layer_name``, ``layer_title``, ``crs``,
        ``format`` and ``styles``.
    """
    if _provider_is_missing_token(provider):
        _ask_token_missing(provider.get("name", ""), "wms")
        return

    token = provider.get("token", "")
    token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
    url = append_token(provider.get("url", "").strip(), token, token_param)

    service_type = provider.get("service_type", "wms")
    layer_service_type = layer_data.get("service_type", service_type)

    try:
        if layer_service_type == "wmts":
            _load_wmts(url, layer_data)
        else:
            _load_wms(url, layer_data)
    except (KeyError, TypeError) as exc:
        _report_load_failure(layer_data.get("layer_title", ""), str(exc))


def _load_wms(url: str, layer_data: dict[str, Any]) -> None:
    """Create and add a WMS layer."""
    params = {
        "url": url,
        "layers": layer_data["layer_name"],
        "format": (layer_data.get("format") or ["image/png"])[0],
        "crs": (layer_data.get("crs") or ["EPSG:3857"])[0],
        "styles": (layer_data["styles"][0] if layer_data.get("styles") else ""),
    }
    uri = QgsDataSourceUri()
    for key, value in params.items():
        uri.setParam(key, value)

    title = layer_data.get("layer_title", layer_data.get("layer_name", "wms"))
    layer = QgsRasterLayer(str(uri.encodedUri(), "utf-8"), title, "wms")
    _add_or_report(layer, title)


def _load_wmts(url: str, layer_data: dict[str, Any]) -> None:
    """Create and add a WMTS layer."""
    uri = QgsDataSourceUri()
    uri.setParam("url", url)
    uri.setParam("layers", layer_data["layer_name"])

    if layer_data.get("crs"):
        uri.setParam("tileMatrixSet", layer_data["crs"][0])
    if layer_data.get("format"):
        uri.setParam("format", layer_data["format"][0])
    uri.setParam(
        "styles", layer_data["styles"][0] if layer_data.get("styles") else ""
    )

    title = layer_data.get("layer_title", layer_data.get("layer_name", "wmts"))
    encoded = str(uri.encodedUri(), "utf-8")
    layer = QgsRasterLayer(encoded, title, "wms")
    _add_or_report(layer, title)


def _add_or_report(layer: QgsRasterLayer, name: str) -> None:
    """Add a valid layer to the project, otherwise report a failure."""
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
    else:
        error = layer.error().message() if layer.error() else ""
        Logger.critical(f"Failed to load layer '{name}': {error}")
        _report_load_failure(name, error)

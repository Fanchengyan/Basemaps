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

import os
import tempfile
from typing import Any
from urllib.parse import urlencode

from qgis.core import (
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
        level=3,  # Qgis.Critical
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

    task = _VectorTileLoadTask(encoded_uri, name, style_url)
    # Keep a reference on the task manager's app-level registry to avoid GC.
    # The task removes itself from this list when it finishes.
    _VectorTileLoadTask._active_tasks.append(task)
    task.taskCompleted.connect(lambda t=task: _VectorTileLoadTask._drop(t))
    task.taskTerminated.connect(lambda t=task: _VectorTileLoadTask._drop(t))
    QgsApplication.taskManager().addTask(task)


class _VectorTileLoadTask(QgsTask):
    """Background task that downloads a vector tile style then adds the layer.

    This is a compact sibling of ``BasemapsDialog.VectorTileLoadTask`` kept
    inside this module so the Browser integration stays independent of the
    dialog module (and its heavy UI imports).
    """

    # Module-level keep-alive list; cleared as tasks finish.
    _active_tasks: list["_VectorTileLoadTask"] = []

    @classmethod
    def _drop(cls, task: "_VectorTileLoadTask") -> None:
        if task in cls._active_tasks:
            cls._active_tasks.remove(task)

    def __init__(self, encoded_uri: str, name: str, style_url: str) -> None:
        super().__init__(
            QCoreApplication.translate(
                "BasemapsBrowser", "Loading vector tile basemap..."
            ),
            QgsTask.Flag.CanCancel,
        )
        self.encoded_uri = encoded_uri
        self.name = name
        self.style_url = style_url
        self.temp_style_path: str | None = None

    def run(self) -> bool:
        """Download the style JSON (best effort). Always returns True."""
        if not self.style_url:
            return True

        style_text = self._fetch_style_text()
        if style_text is None:
            return True

        try:
            fd, self.temp_style_path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(style_text)
        except Exception as error:  # pragma: no cover - best effort
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Failed to write vector tile style '{}': {}",
                ).format(self.style_url, error)
            )
        return True

    def _fetch_style_text(self) -> str | None:
        """Fetch the style JSON body via QGIS network settings."""
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
            return None

        reply = network_request.reply()
        status_code = reply.attribute(_HTTP_STATUS_ATTRIBUTE)
        if status_code and int(status_code) >= 400:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsBrowser",
                    "Failed to download vector tile style '{}': HTTP {}",
                ).format(self.style_url, status_code)
            )
            return None

        content = bytes(reply.content())
        if not content:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def finished(self, result: bool) -> None:
        """Build the vector tile layer on the main thread."""
        uri = self.encoded_uri
        if self.temp_style_path:
            uri += f"&styleUrl=file://{self.temp_style_path}"
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

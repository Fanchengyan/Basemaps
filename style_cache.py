# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""Persistent cache for vector tile style JSON files.

Styles are stored under ``resources/styles/{default|user}/`` following the
same directory convention as the preview thumbnail cache.  Each cached style
consists of two files:

* ``{SafeProvider}_{SafeBasemap}.json``  – the style document
* ``{SafeProvider}_{SafeBasemap}.meta``  – YAML metadata (ETag, timestamp)

Cache validation uses HTTP ETag conditional requests (``If-None-Match``)
so unchanged styles are never re-downloaded.
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtCore import QUrl

from ._vtile_style_util import normalize_style_text
from .messageTool import Logger

# Qt5/Qt6 compatibility for the HTTP-status attribute enum scope.
try:
    _HTTP_STATUS_ATTRIBUTE = QNetworkRequest.Attribute.HttpStatusCodeAttribute
except AttributeError:
    _HTTP_STATUS_ATTRIBUTE = QNetworkRequest.HttpStatusCodeAttribute

_REQUEST_HEADERS = (
    (
        b"User-Agent",
        b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        b"AppleWebKit/537.36 (KHTML, like Gecko) "
        b"Chrome/120.0.0.0 Safari/537.36",
    ),
    (b"Accept", b"application/json, text/plain, */*"),
)

# Module-level singleton, lazily initialised on first access.
_instance: StyleCache | None = None


def safe_file_url(path: str) -> str:
    """Build a properly encoded ``file://`` URL from a local path.

    Spaces in the path (e.g. ``Application Support`` on macOS) would
    break a naive ``file://{path}`` concatenation.  Using
    ``QUrl.fromLocalFile`` guarantees correct percent-encoding.
    """
    from qgis.PyQt.QtCore import QUrl

    return QUrl.fromLocalFile(path).toString()


def get_style_cache() -> StyleCache:
    """Return the module-level :class:`StyleCache` singleton.

    The instance is created on first call using the ``resources/`` directory
    that ships with the plugin (next to this file).
    """
    global _instance
    if _instance is None:
        resources_dir = Path(__file__).resolve().parent / "resources"
        _instance = StyleCache(resources_dir)
    return _instance


class StyleCache:
    """Manages a local file cache for vector tile style JSON.

    Parameters
    ----------
    resources_dir : Path
        Root ``resources/`` directory of the plugin.
    """

    def __init__(self, resources_dir: Path) -> None:
        self._default_dir = resources_dir / "styles" / "default"
        self._user_dir = resources_dir / "styles" / "user"
        self._default_dir.mkdir(parents=True, exist_ok=True)
        self._user_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def get_cached_style(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool = True,
    ) -> Path | None:
        """Return the path to a cached style file, or ``None``."""
        path = self._style_path(provider_name, basemap_name, is_default)
        meta = self._meta_path(path)
        return path if path.exists() and meta.exists() else None

    def get_etag(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool = True,
    ) -> str | None:
        """Return the stored ETag for a cached style, or ``None``."""
        path = self._style_path(provider_name, basemap_name, is_default)
        return self._read_etag(path)

    def save(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool,
        style_text: str,
        etag: str,
        style_url: str = "",
    ) -> None:
        """Persist *style_text* and *etag* to the cache.

        The document is normalized via :func:`normalize_style_text` before
        saving:

        * **Mapbox style** → relative ``sprite`` / ``glyphs`` /
          ``sources[*].url`` are rewritten to absolute URLs against
          *style_url* (otherwise QGIS resolves them against the local
          ``file://`` cache path, where those resources do not exist).
        * **TileJSON** (when *style_url* was mistakenly pointed at a
          ``tiles.json`` endpoint) → a generic Mapbox v8 style is
          synthesized from ``vector_layers`` so the layer renders with
          visible colors instead of an empty style.

        Non-JSON or unrecognized payloads are persisted unchanged as a
        best-effort cache.
        """
        if style_url:
            style_text = normalize_style_text(style_text, style_url, basemap_name)
        path = self._style_path(provider_name, basemap_name, is_default)
        try:
            path.write_text(style_text, encoding="utf-8")
        except OSError as exc:
            Logger.warning(f"Failed to write style cache {path}: {exc}")
            return
        meta = self._meta_path(path)
        try:
            with meta.open("w", encoding="utf-8") as fh:
                yaml.dump(
                    {"etag": etag, "timestamp": time.time()},
                    fh,
                    default_flow_style=False,
                    allow_unicode=True,
                )
        except OSError as exc:
            Logger.warning(f"Failed to write style meta {meta}: {exc}")

    # ------------------------------------------------------------------
    # Deletion helpers (called when basemaps / providers are removed)
    # ------------------------------------------------------------------

    def delete_cached_style(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool = True,
    ) -> None:
        """Delete the cached style and its metadata for one basemap."""
        path = self._style_path(provider_name, basemap_name, is_default)
        self._remove(path)

    def delete_provider_styles(
        self,
        provider_name: str,
        basemaps: list[dict],
        is_default: bool = True,
    ) -> None:
        """Delete all cached styles for every basemap of a provider."""
        for bm in basemaps:
            name = bm.get("name") or bm.get("layer_title", "")
            if name:
                self.delete_cached_style(provider_name, name, is_default)

    # ------------------------------------------------------------------
    # Background ETag validation (used by double-click load task)
    # ------------------------------------------------------------------

    def validate_cache(
        self,
        provider_name: str,
        basemap_name: str,
        is_default: bool,
        style_url: str,
    ) -> bool:
        """Send a conditional GET; update cache on 200.

        Returns ``True`` when the cache was refreshed (new content),
        ``False`` when no update was needed or the request failed.
        """
        cached_etag = self.get_etag(provider_name, basemap_name, is_default)
        if not cached_etag:
            return False

        path = self._style_path(provider_name, basemap_name, is_default)
        request = QNetworkRequest(QUrl(style_url))
        for header, value in _REQUEST_HEADERS:
            request.setRawHeader(header, value)
        request.setRawHeader(b"If-None-Match", cached_etag.encode("utf-8"))

        network_request = QgsBlockingNetworkRequest()
        error_code = network_request.get(request, True)
        if error_code != QgsBlockingNetworkRequest.NoError:
            return False

        reply = network_request.reply()
        status_code = reply.attribute(_HTTP_STATUS_ATTRIBUTE)
        if status_code and int(status_code) == 304:
            return False
        if status_code and int(status_code) >= 400:
            return False

        new_etag = self._reply_etag(reply)
        if not new_etag or new_etag == cached_etag:
            return False

        content = bytes(reply.content())
        if not content:
            return False

        try:
            style_text = content.decode("utf-8")
        except UnicodeDecodeError:
            return False

        self.save(
            provider_name,
            basemap_name,
            is_default,
            style_text,
            new_etag,
            style_url=style_url,
        )
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dir(self, is_default: bool) -> Path:
        return self._default_dir if is_default else self._user_dir

    @staticmethod
    def _safe(text: str) -> str:
        return "".join(c for c in text if c.isalnum())

    def _style_path(
        self, provider_name: str, basemap_name: str, is_default: bool
    ) -> Path:
        return (
            self._dir(is_default)
            / f"{self._safe(provider_name)}_{self._safe(basemap_name)}.json"
        )

    @staticmethod
    def _meta_path(style_path: Path) -> Path:
        return style_path.with_suffix(".meta")

    def _read_etag(self, style_path: Path) -> str | None:
        meta = self._meta_path(style_path)
        if not meta.exists():
            return None
        try:
            with meta.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            return data.get("etag") if isinstance(data, dict) else None
        except (OSError, yaml.YAMLError):
            return None

    @staticmethod
    def _reply_etag(reply) -> str | None:
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

    @staticmethod
    def _remove(path: Path) -> None:
        meta = path.with_suffix(".meta")
        for p in (path, meta):
            if p.exists():
                try:
                    p.unlink()
                except OSError as exc:
                    Logger.warning(f"Failed to delete style cache {p}: {exc}")

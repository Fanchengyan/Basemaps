from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsDataSourceUri,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsNetworkAccessManager,
    QgsRectangle,
    QgsTask,
    QgsVectorTileLayer,
)
from qgis.PyQt.QtCore import QObject, QSize, pyqtSignal, QUrl, Qt
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPen, QPixmap

from .messageTool import Logger
from . import wmts_parser


@dataclass
class VectorPreviewResult:
    """Background vector preview task result.

    Attributes
    ----------
    success : bool
        Whether preview generation succeeded.
    key : str
        Basemap preview key in ``{provider}_{layer}`` format.
    image_path : str
        Saved preview image path when successful, otherwise an empty string.
    error_message : str
        Error details for logging when unsuccessful.
    """

    success: bool
    key: str
    image_path: str
    error_message: str


class VectorPreviewTaskSignals(QObject):
    """Signal container for vector preview background tasks."""

    finished = pyqtSignal(object)


class VectorPreviewTask(QgsTask):
    """Background task that prepares and renders a vector tile preview.

    Parameters
    ----------
    provider_name : str
        Provider display name.
    layer_name : str
        Basemap display name.
    tile_url : str
        Tokenized vector tile source URL.
    style_url : str
        Tokenized style metadata URL.
    preview_path : Path
        Target cache file path.
    """

    def __init__(
        self,
        provider_name: str,
        layer_name: str,
        tile_url: str,
        style_url: str,
        preview_path: Path,
    ) -> None:
        super().__init__(
            f"Rendering vector preview for {provider_name} / {layer_name}",
            QgsTask.CanCancel,
        )
        self.provider_name = provider_name
        self.layer_name = layer_name
        self.tile_url = tile_url
        self.style_url = style_url
        self.preview_path = preview_path
        self.key = f"{provider_name}_{layer_name}"
        self.signals = VectorPreviewTaskSignals()
        self._result = VectorPreviewResult(False, self.key, "", "")

    def run(self) -> bool:
        """Generate the preview in a worker thread."""
        try:
            self.setProgress(10)
            if self.isCanceled():
                self._result = VectorPreviewResult(
                    False, self.key, "", "Task canceled before rendering started"
                )
                return False

            image = PreviewManager.render_vector_preview_image(
                self.tile_url,
                self.style_url,
                self.layer_name,
            )
            if image is None or image.isNull():
                self._result = VectorPreviewResult(
                    False, self.key, "", "Vector preview image is empty"
                )
                return False

            self.setProgress(85)
            self.preview_path.parent.mkdir(parents=True, exist_ok=True)
            if not image.save(str(self.preview_path)):
                self._result = VectorPreviewResult(
                    False,
                    self.key,
                    "",
                    f"Failed to save vector preview to {self.preview_path}",
                )
                return False

            self._result = VectorPreviewResult(
                True, self.key, str(self.preview_path), ""
            )
            self.setProgress(100)
            return True
        except Exception as exc:
            Logger.warning(f"Vector preview task failed for {self.key}: {exc}")
            self._result = VectorPreviewResult(False, self.key, "", str(exc))
            return False

    def finished(self, result: bool) -> None:
        """Emit the task result back to the main thread."""
        if not result and self._result.success:
            self._result = VectorPreviewResult(
                False, self.key, "", "Vector preview task ended unsuccessfully"
            )
        self.signals.finished.emit(self._result)


class PreviewManager(QObject):
    """Manager for fetching and caching basemap preview tiles.

    Parameters
    ----------
    resources_dir : Path
        Path to the resources directory containing previews and icons.

    Attributes
    ----------
    preview_readied : pyqtSignal
        Signal emitted when a preview is ready. Args: (key: str, image_path: str)
    """

    preview_readied = pyqtSignal(str, str)  # key, image_path

    # Wayback uses same imagery across all time layers
    WAYBACK_SHARED_LAYER = "WorldImagery"

    def __init__(self, resources_dir: Path):
        super().__init__()
        self.resources_dir = resources_dir
        self.previews_dir = resources_dir / "previews"
        self.previews_dir.mkdir(parents=True, exist_ok=True)

        # Create separate subdirectories for default/user and xyz/wms
        self._default_xyz_dir = self.previews_dir / "default" / "xyz"
        self._default_wms_dir = self.previews_dir / "default" / "wms"
        self._user_xyz_dir = self.previews_dir / "user" / "xyz"
        self._user_wms_dir = self.previews_dir / "user" / "wms"

        for dir_path in [
            self._default_xyz_dir,
            self._default_wms_dir,
            self._user_xyz_dir,
            self._user_wms_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)

        self.failed_icon_path = resources_dir / "icons" / "error.svg"
        self._pending_tasks: set[str] = set()
        self._active_requests: dict = {}  # Map of request_id -> reply object
        self._request_queue: list = []  # Queue of task dicts
        self._vector_preview_tasks: dict[str, VectorPreviewTask] = {}

        # Track composite downloads: key -> {'received': {index: QImage}, 'total': 4, 'failed': bool}
        self._active_composites: dict = {}

        # Track Wayback waiting keys: shared_key -> list of original keys waiting for this preview
        self._wayback_waiting: dict[str, list[str]] = {}

        # Cache for ResourceURL templates: provider_url -> {layer_name: resource_url}
        self._resource_url_cache: dict[str, dict[str, str]] = {}
        # Track pending capabilities fetch requests: provider_url -> list of waiting tasks
        self._pending_capabilities: dict[str, list[dict]] = {}

        cpu_count = os.cpu_count() or 2
        if cpu_count <= 4:
            self._max_concurrent = 2
        else:
            self._max_concurrent = (cpu_count // 2) + 1
        self._max_concurrent = min(self._max_concurrent, 8)

    def _is_wayback_provider(self, provider_name: str, url: str) -> bool:
        """Check if provider is Esri Wayback (all layers share same preview)."""
        return "wayback" in provider_name.lower() or "wayback" in url.lower()

    def _get_preview_dir(self, service_type: str, is_default: bool) -> Path:
        """Get the preview directory based on service type and provider source.

        Parameters
        ----------
        service_type : str
            Service type: "xyz", "wms", or "wmts"
        is_default : bool
            True if provider is from default directory

        Returns
        -------
        Path
            Directory path for storing previews
        """
        if is_default:
            return (
                self._default_wms_dir
                if service_type in ["wms", "wmts"]
                else self._default_xyz_dir
            )
        return (
            self._user_wms_dir
            if service_type in ["wms", "wmts"]
            else self._user_xyz_dir
        )

    def get_preview_path(
        self,
        provider_name: str,
        layer_name: str,
        service_type: str = "xyz",
        is_default: bool = True,
        url: str = "",
    ) -> Path:
        """Get the cached preview path for a given provider and layer.

        Parameters
        ----------
        provider_name : str
            Name of the provider
        layer_name : str
            Name of the layer/basemap
        service_type : str
            Service type: "xyz", "wms", or "wmts"
        is_default : bool
            True if provider is from default directory
        url : str
            URL of the service (used to detect Wayback)

        Returns
        -------
        Path
            Path to the preview image file
        """
        base_dir = self._get_preview_dir(service_type, is_default)

        # For Wayback, use shared layer name since all layers have same imagery
        if self._is_wayback_provider(provider_name, url):
            layer_name = self.WAYBACK_SHARED_LAYER

        safe_provider = "".join([c for c in provider_name if c.isalnum()])
        safe_layer = "".join([c for c in layer_name if c.isalnum()])
        filename = f"{safe_provider}_{safe_layer}.png"
        return base_dir / filename

    def generate_vector_placeholder(
        self,
        provider_name: str,
        layer_name: str,
        is_default: bool = True,
        url: str = "",
    ) -> QPixmap | None:
        """Generate a placeholder QPixmap for vector tile basemaps.

        Uses QImage+QPainter (proven pattern from _merge_and_save) and returns
        an in-memory pixmap. Placeholder imagery is intentionally not written
        to the preview cache, because vector previews should later be replaced
        with a rendered thumbnail.

        Parameters
        ----------
        provider_name : str
            Name of the provider. Unused for drawing, kept for call-site
            compatibility.
        layer_name : str
            Name of the vector basemap. Unused for drawing, kept for call-site
            compatibility.
        is_default : bool
            Whether the provider comes from the default catalog. Unused for
            drawing, kept for call-site compatibility.
        url : str
            Basemap URL. Unused for drawing, kept for call-site compatibility.

        Returns
        -------
        QPixmap | None
            Placeholder preview pixmap, or ``None`` when generation fails.
        """
        try:
            # Draw using QImage (proven pattern from _merge_and_save).
            # Use Format_RGB32 to avoid any premultiplied-alpha conversion
            # issues when converting to QPixmap.
            img = QImage(256, 256, QImage.Format_RGB32)
            img.fill(QColor(235, 240, 248))

            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing)

            # Grid lines
            painter.setPen(QPen(QColor(200, 212, 230), 0.5))
            for i in range(1, 8):
                pos = i * 32
                painter.drawLine(0, pos, 256, pos)
                painter.drawLine(pos, 0, pos, 256)

            painter.setPen(QPen(QColor(175, 190, 215), 1.5))
            painter.drawRect(10, 10, 236, 236)

            # Map icon: square + crosshair + arrow
            hw = 24  # half width
            cx, cy = 128, 128
            painter.setPen(QPen(QColor(140, 160, 195), 2.5))
            painter.drawRect(cx - hw, cy - hw, hw * 2, hw * 2)
            painter.drawLine(cx, cy - hw + 10, cx, cy + hw - 10)
            painter.drawLine(cx - hw + 10, cy, cx + hw - 10, cy)
            # Arrow
            painter.setPen(QPen(QColor(140, 160, 195), 2))
            top = cy - hw - 12
            base = cy - hw - 2
            painter.drawLine(cx, top, cx - 10, base)
            painter.drawLine(cx, top, cx + 10, base)
            painter.drawLine(cx - 10, base, cx + 10, base)

            painter.end()
            pixmap = QPixmap.fromImage(img)
            if pixmap.isNull():
                Logger.warning(f"QPixmap load returned null for {layer_name}")
                return None
            return pixmap
        except Exception as e:
            Logger.warning(f"Failed to generate vector placeholder: {e}")
            return None

    def request_vector_preview(
        self,
        provider_name: str,
        layer_name: str,
        tile_url: str,
        style_url: str = "",
        is_default: bool = True,
    ) -> None:
        """Queue rendering of a vector tile basemap preview.

        Parameters
        ----------
        provider_name : str
            Name of the provider.
        layer_name : str
            Name of the vector basemap.
        tile_url : str
            Tokenized vector tile source URL.
        style_url : str, default=""
            Tokenized vector tile style URL, when available.
        is_default : bool, default=True
            Whether the provider belongs to the default catalog.
        """
        preview_path = self.get_preview_path(
            provider_name, layer_name, "xyz", is_default, tile_url
        )
        key = f"{provider_name}_{layer_name}"
        Logger.info(f"Queueing vector preview for {key}")

        if preview_path.exists():
            if self._is_invalid_vector_preview_cache(preview_path):
                try:
                    preview_path.unlink()
                except OSError as exc:
                    Logger.warning(
                        f"Failed to remove invalid vector preview cache {preview_path}: {exc}"
                    )
            else:
                self.preview_readied.emit(key, str(preview_path))
                return

        if key in self._pending_tasks:
            return

        self._pending_tasks.add(key)
        task = VectorPreviewTask(
            provider_name=provider_name,
            layer_name=layer_name,
            tile_url=tile_url,
            style_url=style_url,
            preview_path=preview_path,
        )
        task.signals.finished.connect(self._on_vector_preview_task_finished)
        self._vector_preview_tasks[key] = task
        QgsApplication.taskManager().addTask(task)

    def request_preview(
        self,
        provider_name: str,
        layer_name: str,
        url: str,
        service_type: str = "xyz",
        layer_data: dict | None = None,
        is_default: bool = True,
    ) -> None:
        """Request a preview, fetching from network if not cached.

        Parameters
        ----------
        provider_name : str
            Name of the provider
        layer_name : str
            Name of the layer/basemap (used for key matching)
        url : str
            URL of the tile service
        service_type : str
            Service type: "xyz", "wms", or "wmts"
        layer_data : dict | None
            Layer data dictionary (for WMS/WMTS)
        is_default : bool
            True if provider is from default directory
        """
        # For Wayback, use shared layer name for path but keep original for key
        is_wayback = self._is_wayback_provider(provider_name, url)
        path_layer_name = self.WAYBACK_SHARED_LAYER if is_wayback else layer_name

        preview_path = self.get_preview_path(
            provider_name, path_layer_name, service_type, is_default, url
        )
        # Key uses original layer_name for proper UI matching
        key = f"{provider_name}_{layer_name}"

        if preview_path.exists():
            self.preview_readied.emit(key, str(preview_path))
            return

        # For Wayback, check if we already have a pending request for shared preview
        if is_wayback:
            shared_key = f"{provider_name}_{self.WAYBACK_SHARED_LAYER}"
            if shared_key in self._pending_tasks:
                # Already fetching - add this key to waiting list
                if shared_key not in self._wayback_waiting:
                    self._wayback_waiting[shared_key] = []
                self._wayback_waiting[shared_key].append(key)
                return

        if key in self._pending_tasks:
            return

        self._pending_tasks.add(key)
        if is_wayback:
            shared_key = f"{provider_name}_{self.WAYBACK_SHARED_LAYER}"
            self._pending_tasks.add(shared_key)
            # Initialize waiting list with the first key
            self._wayback_waiting[shared_key] = [key]
            Logger.info(
                f"Wayback preview requested: shared_key={shared_key}, first_key={key}"
            )

        task = {
            "type": "single",
            "provider": provider_name,
            "layer": layer_name,
            "url": url,
            "service_type": service_type,
            "layer_data": layer_data,
            "path": preview_path,
            "key": key,
            "is_default": is_default,
            "is_wayback": is_wayback,
            "retry_as_composite": True,
        }

        # For WMTS without resource_url, try to get it from cache
        if service_type == "wmts" and layer_data:
            resource_url = layer_data.get("resource_url")
            if not resource_url:
                layer_name_for_cache = layer_data.get("layer_name", "")
                cached_url = self._resource_url_cache.get(url, {}).get(
                    layer_name_for_cache
                )
                if cached_url:
                    layer_data["resource_url"] = cached_url
                    Logger.info(f"Using cached ResourceURL for {layer_name_for_cache}")

        self._request_queue.append(task)
        self._process_queue()

    def delete_preview(
        self,
        provider_name: str,
        layer_name: str,
        service_type: str = "xyz",
        is_default: bool = False,
        url: str = "",
    ) -> bool:
        """Delete a preview image file.

        Parameters
        ----------
        provider_name : str
            Name of the provider
        layer_name : str
            Name of the layer/basemap
        service_type : str
            Service type: "xyz", "wms", or "wmts"
        is_default : bool
            True if provider is from default directory
        url : str
            URL of the service (used to detect Wayback)

        Returns
        -------
        bool
            True if file was deleted, False otherwise
        """
        preview_path = self.get_preview_path(
            provider_name, layer_name, service_type, is_default, url
        )
        if preview_path.exists():
            try:
                preview_path.unlink()
                Logger.info(f"Deleted preview: {preview_path}")
                return True
            except OSError as e:
                Logger.warning(f"Failed to delete preview {preview_path}: {e}")
        return False

    def delete_provider_previews(
        self,
        provider_name: str,
        basemaps_or_layers: list[dict],
        service_type: str = "xyz",
        is_default: bool = False,
        url: str = "",
    ) -> int:
        """Delete all preview images for a provider.

        Parameters
        ----------
        provider_name : str
            Name of the provider
        basemaps_or_layers : list[dict]
            List of basemap/layer dicts with 'name' or 'layer_title' keys
        service_type : str
            Service type: "xyz", "wms", or "wmts"
        is_default : bool
            True if provider is from default directory
        url : str
            URL of the service (used to detect Wayback)

        Returns
        -------
        int
            Number of deleted preview files
        """
        deleted_count = 0

        # For Wayback, just delete the shared preview once
        if self._is_wayback_provider(provider_name, url):
            if self.delete_preview(
                provider_name, self.WAYBACK_SHARED_LAYER, service_type, is_default, url
            ):
                deleted_count = 1
            return deleted_count

        for item in basemaps_or_layers:
            # XYZ uses 'name', WMS/WMTS uses 'layer_title'
            layer_name = item.get("name") or item.get("layer_title", "")
            if layer_name and self.delete_preview(
                provider_name, layer_name, service_type, is_default, url
            ):
                deleted_count += 1

        return deleted_count

    def _process_queue(self) -> None:
        """Process queued requests up to max concurrent limit."""
        while len(self._active_requests) < self._max_concurrent and self._request_queue:
            task = self._request_queue.pop(0)
            key = task["key"]

            if task["type"] == "single":
                fetch_url = self._construct_preview_url(
                    task["url"], task["service_type"], task["layer_data"], z=0, x=0, y=0
                )
                if not fetch_url:
                    self._on_fetch_failed(task)
                    continue

                req_id = f"{key}_single"
                self._start_request(fetch_url, req_id, task)

            elif task["type"] == "composite":
                z = task.get("z", 1)
                # Tile coords for different zoom levels, picking 4 tiles from
                # the map center at higher zooms where coverage is likely
                if z == 1:
                    tiles = [(0, 0, 0), (1, 0, 1), (0, 1, 2), (1, 1, 3)]
                elif z == 2:
                    tiles = [(1, 1, 0), (2, 1, 1), (1, 2, 2), (2, 2, 3)]
                else:
                    # z >= 3: pick center 4 from the 2^z × 2^z grid
                    half = (1 << z) // 2 - 1
                    tiles = [
                        (half, half, 0),
                        (half + 1, half, 1),
                        (half, half + 1, 2),
                        (half + 1, half + 1, 3),
                    ]

                self._active_composites[key] = {
                    "received": {},
                    "failed": set(),
                    "total": len(tiles),
                    "tiles": tiles,
                    "z": z,
                    "retry_count": 0,
                    "task": task,
                }

                for x, y, idx in tiles:
                    fetch_url = self._construct_preview_url(
                        task["url"],
                        task["service_type"],
                        task["layer_data"],
                        z=z,
                        x=x,
                        y=y,
                    )
                    if fetch_url:
                        req_id = f"{key}_comp_{idx}"
                        self._start_request(fetch_url, req_id, task, composite_idx=idx)
                    else:
                        # Count as an immediate failure
                        comp_data = self._active_composites.get(key)
                        if comp_data:
                            comp_data["failed"].add(idx)
                            comp_data["completed"] = comp_data.get("completed", 0) + 1

                # Check if all failed immediately (no requests started)
                comp_data = self._active_composites.get(key)
                if comp_data and comp_data.get("completed", 0) >= comp_data["total"]:
                    self._handle_composite_complete(key, task)

    def _start_request(
        self, url: str, req_id: str, task: dict, composite_idx: int = -1
    ) -> None:
        nam = QgsNetworkAccessManager.instance()
        request = QNetworkRequest(QUrl(url))

        request.setHeader(
            QNetworkRequest.UserAgentHeader,
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36 QGIS/3.0.0",
        )

        # Enable automatic redirect following
        # Qt5: FollowRedirectsAttribute (deprecated in Qt6)
        # Qt6: RedirectPolicyAttribute
        try:
            # Try Qt6 style first
            from qgis.PyQt.QtNetwork import QNetworkRequest as QNR

            if hasattr(QNR, "RedirectPolicyAttribute"):
                request.setAttribute(
                    QNR.RedirectPolicyAttribute, 1
                )  # 1 = NoLessSafeRedirectPolicy
            elif hasattr(QNR, "FollowRedirectsAttribute"):
                request.setAttribute(QNR.FollowRedirectsAttribute, True)
        except Exception:
            pass

        reply = nam.get(request)
        reply.finished.connect(
            lambda r=reply,
            rid=req_id,
            t=task,
            c_idx=composite_idx: self._on_reply_finished(r, rid, t, c_idx)
        )
        self._active_requests[req_id] = reply

    def _construct_preview_url(
        self,
        url: str,
        service_type: str,
        layer_data: dict | None = None,
        z: int = 0,
        x: int = 0,
        y: int = 0,
    ) -> str | None:
        """Construct a valid URL for a tile at given coordinates."""
        if service_type == "xyz":
            if "{q}" in url:
                quadkey = ""
                for i in range(z, 0, -1):
                    digit = 0
                    mask = 1 << (i - 1)
                    if (x & mask) != 0:
                        digit += 1
                    if (y & mask) != 0:
                        digit += 2
                    quadkey += str(digit)
                return url.replace("{q}", quadkey)

            preview_url = (
                url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            )

            if "{-y}" in url:
                max_y = (1 << z) - 1
                inv_y = max_y - y
                preview_url = (
                    url.replace("{z}", str(z))
                    .replace("{x}", str(x))
                    .replace("{-y}", str(inv_y))
                )

            import re

            switch_pattern = r"\{switch:([^}]+)\}"
            match = re.search(switch_pattern, preview_url)
            if match:
                options = match.group(1).split(",")
                preview_url = preview_url.replace(match.group(0), options[0])

            return preview_url

        elif service_type == "wms":
            if not layer_data:
                return None
            layer_name = layer_data.get("layer_name", "")
            if not layer_name:
                return None

            crs_list = layer_data.get("crs", [])
            crs = (
                "EPSG:3857"
                if "EPSG:3857" in crs_list
                else ("EPSG:4326" if "EPSG:4326" in crs_list else crs_list[0])
            )

            format_list = layer_data.get("format", [])
            img_format = (
                "image/png"
                if "image/png" in format_list
                else (format_list[0] if format_list else "image/png")
            )

            min_x, min_y, max_x, max_y = -180, -90, 180, 90
            if crs == "EPSG:3857":
                min_x, min_y, max_x, max_y = (
                    -20037508.34,
                    -20037508.34,
                    20037508.34,
                    20037508.34,
                )

            if z == 1:
                mid_x = (min_x + max_x) / 2
                mid_y = (min_y + max_y) / 2
                if x == 0 and y == 0:
                    bbox = f"{min_x},{mid_y},{mid_x},{max_y}"
                elif x == 1 and y == 0:
                    bbox = f"{mid_x},{mid_y},{max_x},{max_y}"
                elif x == 0 and y == 1:
                    bbox = f"{min_x},{min_y},{mid_x},{mid_y}"
                elif x == 1 and y == 1:
                    bbox = f"{mid_x},{min_y},{max_x},{mid_y}"
                else:
                    bbox = f"{min_x},{min_y},{max_x},{max_y}"
            else:
                bbox = f"{min_x},{min_y},{max_x},{max_y}"

            params = {
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "REQUEST": "GetMap",
                "LAYERS": layer_name,
                "CRS": crs,
                "BBOX": bbox,
                "WIDTH": "256",
                "HEIGHT": "256",
                "FORMAT": img_format,
                "STYLES": layer_data.get("styles", [""])[0] or "",
            }

            from urllib.parse import urlencode

            separator = "&" if "?" in url else "?"
            return f"{url}{separator}{urlencode(params)}"

        elif service_type == "wmts":
            if not layer_data:
                return None
            layer_name = layer_data.get("layer_name", "")
            if not layer_name:
                return None

            crs_list = layer_data.get("crs", [])
            if not crs_list:
                return None

            # Use first CRS to match QGIS layer loading
            tile_matrix_set = crs_list[0]

            format_list = layer_data.get("format", [])
            # Filter to image formats only (skip vector-tile etc.)
            image_formats = [f for f in format_list if f.startswith("image/")]
            if not image_formats:
                return None
            img_format = (
                "image/jpeg"
                if "image/jpeg" in image_formats
                else image_formats[0]
            )
            styles = layer_data.get("styles", ["default"])
            style = styles[0] if styles else "default"

            # Check for ResourceURL template (RESTful WMTS)
            resource_url = layer_data.get("resource_url")
            if resource_url:
                # Build URL from ResourceURL template
                preview_url = resource_url
                preview_url = preview_url.replace("{Layer}", layer_name)
                preview_url = preview_url.replace("{layer}", layer_name)
                preview_url = preview_url.replace("{TileMatrixSet}", tile_matrix_set)
                preview_url = preview_url.replace("{TileMatrix}", str(z))
                preview_url = preview_url.replace("{TileRow}", str(y))
                preview_url = preview_url.replace("{TileCol}", str(x))
                preview_url = preview_url.replace("{Style}", style)
                preview_url = preview_url.replace("{style}", style)
                # NASA GIBS uses {Time} for temporal layers
                if "{Time}" in preview_url:
                    preview_url = preview_url.replace("{Time}", "default")
                preview_url = self._append_auth_query_params(preview_url, url)
                Logger.info(f"WMTS preview URL (ResourceURL): {preview_url}")
                return preview_url

            # Fallback to KVP (Key-Value Pair) style URL
            base_url = (
                url.replace("/WMTSCapabilities.xml", "")
                .replace("WMTSCapabilities.xml", "")
                .rstrip("/")
            )
            params = {
                "SERVICE": "WMTS",
                "REQUEST": "GetTile",
                "VERSION": "1.0.0",
                "LAYER": layer_name,
                "STYLE": style,
                "TILEMATRIXSET": tile_matrix_set,
                "TILEMATRIX": str(z),
                "TILEROW": str(y),
                "TILECOL": str(x),
                "FORMAT": img_format,
            }
            from urllib.parse import urlencode

            separator = "&" if "?" in base_url else "?"
            return f"{base_url}{separator}{urlencode(params)}"

        return None

    @classmethod
    def _is_invalid_vector_preview_cache(cls, preview_path: Path) -> bool:
        """Detect placeholder or blank previews that should not be reused.

        Parameters
        ----------
        preview_path : Path
            Candidate cached preview path.

        Returns
        -------
        bool
            ``True`` when the cache file is a placeholder or a blank render.
        """
        image = QImage(str(preview_path))
        if image.isNull():
            return True

        if cls._is_legacy_vector_placeholder_image(image):
            return True

        return cls._is_blank_vector_preview_image(image)

    @staticmethod
    def _is_legacy_vector_placeholder_image(image: QImage) -> bool:
        """Check whether an image matches the old vector placeholder."""
        if image.isNull() or image.size() != QSize(256, 256):
            return False

        expected_pixels = {
            (16, 16): QColor(235, 240, 248),
            (32, 32): QColor(200, 212, 230),
            (128, 128): QColor(140, 160, 195),
        }
        for (x, y), expected in expected_pixels.items():
            if QColor(image.pixel(x, y)) != expected:
                return False
        return True

    @staticmethod
    def _is_blank_vector_preview_image(image: QImage) -> bool:
        """Check whether a rendered vector preview is effectively blank.

        Parameters
        ----------
        image : QImage
            Preview image to inspect.

        Returns
        -------
        bool
            ``True`` when the image contains almost no meaningful content.
        """
        if image.isNull() or image.size() != QSize(256, 256):
            return False

        sample_counts: dict[tuple[int, int, int], int] = {}
        for y in range(0, image.height(), 4):
            for x in range(0, image.width(), 4):
                color = QColor(image.pixel(x, y))
                key = (color.red(), color.green(), color.blue())
                sample_counts[key] = sample_counts.get(key, 0) + 1

        if not sample_counts:
            return True

        background_rgb = max(sample_counts.items(), key=lambda item: item[1])[0]
        background = QColor(*background_rgb)
        total_samples = 0
        distinct_samples = 0

        for y in range(0, image.height(), 4):
            for x in range(0, image.width(), 4):
                color = QColor(image.pixel(x, y))
                total_samples += 1
                delta = max(
                    abs(color.red() - background.red()),
                    abs(color.green() - background.green()),
                    abs(color.blue() - background.blue()),
                )
                if delta > 25:
                    distinct_samples += 1

        if total_samples == 0:
            return True

        distinct_ratio = distinct_samples / total_samples
        return distinct_ratio < 0.012

    @classmethod
    def render_vector_preview_image(
        cls,
        tile_url: str,
        style_url: str,
        layer_name: str,
    ) -> QImage | None:
        """Render a vector tile basemap thumbnail off-screen.

        Parameters
        ----------
        tile_url : str
            Tokenized vector tile source URL.
        style_url : str
            Tokenized vector style URL, when available.
        layer_name : str
            Display name for the temporary layer.

        Returns
        -------
        QImage | None
            Rendered preview image, or ``None`` when the layer cannot be
            rendered.
        """
        resolved_style_path = cls._prepare_vector_style_file(
            tile_url, style_url, layer_name
        )
        uri = QgsDataSourceUri()
        uri.setParam("type", "xyz")
        uri.setParam("url", tile_url)
        encoded_uri = str(uri.encodedUri(), "utf-8")
        if resolved_style_path:
            encoded_uri += f"&styleUrl=file://{resolved_style_path}"
        elif style_url:
            encoded_uri += f"&styleUrl={style_url}"

        layer = QgsVectorTileLayer(encoded_uri, layer_name)
        if not layer.isValid():
            Logger.warning(f"Vector preview layer is invalid for {layer_name}")
            cls._cleanup_temp_style_file(resolved_style_path)
            return None

        if resolved_style_path or style_url:
            try:
                layer.loadDefaultStyle()
            except Exception as exc:
                Logger.warning(
                    f"Failed to load vector style for preview {layer_name}: {exc}"
                )

        try:
            # z=0 → full; z=1 → half; z=2 → 1/4; z=3 → 1/8; z=4 → 1/16
            full = 20037508.3427892
            zoom_extents = [full, full / 2, full / 4, full / 8, full / 16]
            max_retries = 5

            for z_idx, half_size in enumerate(zoom_extents):
                z_label = z_idx  # z=0, z=1, z≈2, z≈3, z≈4
                for retry in range(max_retries):
                    rendered_image = cls._render_vector_layer_image(layer, half_size)
                    if rendered_image is not None and not rendered_image.isNull():
                        Logger.info(
                            f"Vector preview rendered for {layer_name} "
                            f"at z≈{z_label} (retry {retry})"
                        )
                        return rendered_image

                    if retry < max_retries - 1:
                        Logger.info(
                            f"Vector preview blank for {layer_name} "
                            f"at z≈{z_label}, retry {retry + 1}/{max_retries}"
                        )
                        if hasattr(layer, "reload"):
                            layer.reload()

                Logger.info(
                    f"Vector preview z≈{z_label} exhausted for {layer_name}, "
                    f"escalating to next zoom"
                )

            Logger.warning(f"Vector preview remained blank for {layer_name}")
            return None
        finally:
            cls._cleanup_temp_style_file(resolved_style_path)

    def _on_vector_preview_task_finished(self, result: object) -> None:
        """Handle completion of a background vector preview task.

        Parameters
        ----------
        result : object
            Task result payload, expected to be ``VectorPreviewResult``.
        """
        if not isinstance(result, VectorPreviewResult):
            Logger.warning("Vector preview task returned an unexpected result type")
            return

        self._vector_preview_tasks.pop(result.key, None)
        self._pending_tasks.discard(result.key)

        if result.success and result.image_path:
            Logger.info(f"Vector preview saved for {result.key}: {result.image_path}")
            self.preview_readied.emit(result.key, result.image_path)
            return

        Logger.warning(
            f"Vector preview failed for {result.key}: {result.error_message or 'unknown error'}"
        )
        self.preview_readied.emit(result.key, str(self.failed_icon_path))

    @staticmethod
    def _render_vector_layer_image(
        layer: QgsVectorTileLayer, half_size: float
    ) -> QImage | None:
        """Render a vector tile layer at a single zoom extent.

        Parameters
        ----------
        layer : QgsVectorTileLayer
            The vector tile layer to render.
        half_size : float
            Half the extent size in meters (EPSG:3857).
            Smaller = higher zoom.
        """
        map_settings = QgsMapSettings()
        map_settings.setLayers([layer])
        map_settings.setBackgroundColor(QColor(245, 248, 252))
        map_settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        map_settings.setExtent(
            QgsRectangle(-half_size, -half_size, half_size, half_size)
        )
        map_settings.setOutputSize(QSize(256, 256))
        map_settings.setOutputDpi(96)

        render_job = QgsMapRendererSequentialJob(map_settings)
        render_job.start()
        render_job.waitForFinished()

        rendered_image = render_job.renderedImage()
        if rendered_image.isNull():
            return None
        if PreviewManager._is_blank_vector_preview_image(rendered_image):
            return None
        return rendered_image

    @classmethod
    def _prepare_vector_style_file(
        cls, tile_url: str, style_url: str, layer_name: str
    ) -> str | None:
        """Resolve a local style JSON file for vector preview rendering."""
        style_metadata_url = style_url or cls._derive_tilejson_url(tile_url)
        if not style_metadata_url:
            return None

        payload = cls._fetch_json_payload(style_metadata_url)
        if not payload:
            return None

        if cls._looks_like_mapbox_style(payload):
            # ArcGIS/Esri styles reference server-side sprites & glyphs;
            # let QGIS load them directly via remote URL instead of file://
            if "arcgis" in style_metadata_url.lower() or "esri" in style_metadata_url.lower():
                return None
            return cls._write_temp_json(payload)

        if cls._looks_like_tilejson(payload):
            generated_style = cls._build_generic_vector_style(payload, layer_name)
            if generated_style:
                return cls._write_temp_json(generated_style)

        Logger.warning(
            f"Unsupported vector style metadata for {layer_name}: {style_metadata_url}"
        )
        return None

    @staticmethod
    def _fetch_json_payload(url: str) -> dict | None:
        """Fetch a JSON document for vector preview styling."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        try:
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            Logger.warning(f"Failed to fetch vector style metadata {url}: {exc}")
            return None

        if not isinstance(payload, dict):
            Logger.warning(f"Vector style metadata is not a JSON object: {url}")
            return None
        return payload

    @staticmethod
    def _looks_like_mapbox_style(payload: dict) -> bool:
        """Return whether a JSON payload resembles a Mapbox style."""
        return bool(
            payload.get("version") and payload.get("layers") and payload.get("sources")
        )

    @staticmethod
    def _looks_like_tilejson(payload: dict) -> bool:
        """Return whether a JSON payload resembles TileJSON metadata."""
        return bool(payload.get("tiles"))

    @classmethod
    def _build_generic_vector_style(
        cls, tilejson_payload: dict, layer_name: str
    ) -> dict | None:
        """Build a generic preview style from TileJSON metadata."""
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
            fill_color, line_color = cls._layer_palette(source_layer_name, index)
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

    @staticmethod
    def _layer_palette(source_layer_name: str, index: int) -> tuple[str, str]:
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

    @staticmethod
    def _derive_tilejson_url(tile_url: str) -> str:
        """Derive a TileJSON metadata URL from a tile URL pattern."""
        suffixes = [
            "/{z}/{x}/{y}.pbf",
            "/{z}/{y}/{x}.pbf",
            "/{z}/{x}/{y}.mvt",
            "/{z}/{y}/{x}.mvt",
        ]
        for suffix in suffixes:
            if suffix in tile_url:
                return tile_url.replace(suffix, "/tiles.json")
        return ""

    @staticmethod
    def _write_temp_json(payload: dict) -> str | None:
        """Persist JSON payload to a temporary file."""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
                encoding="utf-8",
            ) as handle:
                json.dump(payload, handle)
                return handle.name
        except OSError as exc:
            Logger.warning(f"Failed to create temporary style file: {exc}")
            return None

    @staticmethod
    def _cleanup_temp_style_file(path: str | None) -> None:
        """Delete a temporary preview style file if it exists."""
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            Logger.warning(f"Failed to remove temporary style file {path}: {exc}")

    @staticmethod
    def _append_auth_query_params(tile_url: str, provider_url: str) -> str:
        """Append provider authentication query parameters to a tile URL.

        Parameters
        ----------
        tile_url : str
            Constructed tile URL.
        provider_url : str
            Provider URL that may contain authentication query parameters.

        Returns
        -------
        str
            Tile URL with authentication query parameters appended.
        """
        provider_parts = urlsplit(provider_url)
        provider_params = parse_qsl(provider_parts.query, keep_blank_values=True)
        if not provider_params:
            return tile_url

        ignored_params = {"service", "request", "version"}
        auth_params = [
            (key, value)
            for key, value in provider_params
            if key.lower() not in ignored_params
        ]
        if not auth_params:
            return tile_url

        tile_parts = urlsplit(tile_url)
        existing_keys = {key for key, _ in parse_qsl(tile_parts.query)}
        merged_params = parse_qsl(tile_parts.query, keep_blank_values=True)
        merged_params.extend(
            (key, value) for key, value in auth_params if key not in existing_keys
        )

        return urlunsplit(
            (
                tile_parts.scheme,
                tile_parts.netloc,
                tile_parts.path,
                urlencode(merged_params),
                tile_parts.fragment,
            )
        )

    def _on_reply_finished(
        self, reply, req_id: str, task: dict, composite_idx: int
    ) -> None:
        content = reply.readAll()
        error_code = reply.error()
        http_status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        reply.deleteLater()
        self._active_requests.pop(req_id, None)

        key = task["key"]

        success = False
        image = QImage()
        if content and len(content) > 50:
            image.loadFromData(content)
            if not image.isNull() and image.width() > 10:
                success = True

        # Log for WMTS requests (including Wayback)
        if task.get("service_type") == "wmts":
            if success:
                Logger.info(
                    f"WMTS tile fetch OK - key: {key}, idx: {composite_idx}, size: {len(content)}"
                )
            else:
                Logger.info(
                    f"WMTS tile fetch FAILED - key: {key}, idx: {composite_idx}, HTTP: {http_status}, "
                    f"error: {error_code}, size: {len(content) if content else 0}"
                )

        if task["type"] == "single":
            if success:
                self._finalize_image(task, image)
            elif task.get("retry_as_composite", False):
                Logger.info(f"Preview z=0 failed for {key}, retrying as composite z=1")
                task["type"] = "composite"
                task["z"] = 1
                task["retry_as_composite"] = False
                self._request_queue.insert(0, task)
                self._process_queue()
            else:
                Logger.warning(f"Preview failed for {key}")
                self._on_fetch_failed(task)

        elif task["type"] == "composite":
            comp_data = self._active_composites.get(key)
            if not comp_data:
                return

            if success:
                comp_data["received"][composite_idx] = image
            else:
                comp_data["failed"].add(composite_idx)

            comp_data["completed"] = comp_data.get("completed", 0) + 1

            if comp_data["completed"] >= comp_data["total"]:
                self._handle_composite_complete(key, task)

            self._process_queue()

    def _handle_composite_complete(self, key: str, task: dict) -> None:
        """Process a composite once all in-flight tiles have reported."""
        comp_data = self._active_composites.get(key)
        if not comp_data:
            return

        received = comp_data["received"]
        failed = comp_data.get("failed", set())
        total = comp_data["total"]

        # All tiles succeeded → done
        if len(received) == total:
            self._active_composites.pop(key, None)
            self._merge_and_save(task, received)
            return

        # All tiles failed → escalate zoom or give up
        if len(received) == 0:
            self._active_composites.pop(key, None)
            current_z = task.get("z", 1)
            if current_z < 3:
                Logger.info(
                    f"Composite z={current_z} all failed for {key}, "
                    f"retrying z={current_z + 1}"
                )
                task["z"] = current_z + 1
                self._request_queue.insert(0, task)
                self._process_queue()
            else:
                Logger.warning(
                    f"Composite preview failed for {key} - all tiles failed at max zoom"
                )
                self._on_fetch_failed(task)
            return

        # Partial: some succeeded, some failed
        if comp_data.get("retry_count", 0) < 5:
            # Retry each failed tile once
            comp_data["retry_count"] = comp_data.get("retry_count", 0) + 1
            comp_data["completed"] = len(received)
            retry_failed = set(failed)
            comp_data["failed"] = set()
            z = comp_data["z"]
            tiles = comp_data["tiles"]
            Logger.info(
                f"Composite z={z} partial for {key}: "
                f"{len(received)}/{total} ok, retrying {len(retry_failed)} failed"
            )
            for x, y, idx in tiles:
                if idx in retry_failed:
                    fetch_url = self._construct_preview_url(
                        task["url"],
                        task["service_type"],
                        task["layer_data"],
                        z=z,
                        x=x,
                        y=y,
                    )
                    if fetch_url:
                        req_id = f"{key}_comp_retry_{idx}"
                        self._start_request(fetch_url, req_id, task, composite_idx=idx)
                    else:
                        comp_data["failed"].add(idx)
                        comp_data["completed"] = comp_data.get("completed", 0) + 1
            # Check if all retries failed immediately
            if comp_data.get("completed", 0) >= total:
                self._handle_composite_complete(key, task)
        else:
            # Already retried — accept partial, but also try next zoom
            # to get better coverage (higher zoom = smaller, denser tiles)
            self._active_composites.pop(key, None)
            Logger.info(
                f"Composite z={comp_data['z']} accepted partial for {key}: "
                f"{len(received)}/{total} tiles"
            )
            self._merge_and_save(task, received)

            current_z = task.get("z", 1)
            if current_z < 3:
                Logger.info(
                    f"Composite z={current_z} partial for {key}, "
                    f"also trying z={current_z + 1}"
                )
                task["z"] = current_z + 1
                self._request_queue.insert(0, task)
                self._process_queue()

    def _merge_and_save(self, task: dict, images: dict) -> None:
        canvas = QImage(512, 512, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(0)
        painter = QPainter(canvas)

        positions = {0: (0, 0), 1: (256, 0), 2: (0, 256), 3: (256, 256)}

        for idx, img in images.items():
            if img and not img.isNull():
                x, y = positions.get(idx, (0, 0))
                painter.drawImage(x, y, img)

        painter.end()

        final_img = canvas.scaled(
            256, 256, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )

        key = task["key"]
        path = task["path"]
        is_wayback = task.get("is_wayback", False)
        provider_name = task.get("provider", "")

        if final_img.save(str(path)):
            self._pending_tasks.discard(key)
            if is_wayback:
                shared_key = f"{provider_name}_{self.WAYBACK_SHARED_LAYER}"
                self._pending_tasks.discard(shared_key)
                # Emit signal for all waiting keys
                waiting_keys = self._wayback_waiting.pop(shared_key, [])
                Logger.info(
                    f"Wayback preview saved to {path}, emitting for {len(waiting_keys)} keys"
                )
                for wkey in waiting_keys:
                    self.preview_readied.emit(wkey, str(path))
            else:
                self.preview_readied.emit(key, str(path))
            Logger.info(f"Composite preview saved for {key}")
        else:
            self._on_fetch_failed(task)

    def _finalize_image(self, task: dict, image: QImage) -> None:
        key = task["key"]
        path = task["path"]
        is_wayback = task.get("is_wayback", False)
        provider_name = task.get("provider", "")

        if image.save(str(path)):
            self._pending_tasks.discard(key)
            if is_wayback:
                shared_key = f"{provider_name}_{self.WAYBACK_SHARED_LAYER}"
                self._pending_tasks.discard(shared_key)
                # Emit signal for all waiting keys
                waiting_keys = self._wayback_waiting.pop(shared_key, [])
                for wkey in waiting_keys:
                    self.preview_readied.emit(wkey, str(path))
            else:
                self.preview_readied.emit(key, str(path))
        else:
            self._on_fetch_failed(task)

        self._process_queue()

    def _on_fetch_failed(self, task: dict) -> None:
        key = task["key"]
        is_wayback = task.get("is_wayback", False)
        provider_name = task.get("provider", "")

        self._pending_tasks.discard(key)
        if is_wayback:
            shared_key = f"{provider_name}_{self.WAYBACK_SHARED_LAYER}"
            self._pending_tasks.discard(shared_key)
            # Emit failed signal for all waiting keys
            waiting_keys = self._wayback_waiting.pop(shared_key, [])
            for wkey in waiting_keys:
                self.preview_readied.emit(wkey, str(self.failed_icon_path))
        else:
            self.preview_readied.emit(key, str(self.failed_icon_path))
        self._active_composites.pop(key, None)
        self._process_queue()

    def _fetch_capabilities_async(self, provider_url: str, task: dict) -> None:
        """Fetch WMTS capabilities asynchronously to get ResourceURL templates.

        Parameters
        ----------
        provider_url : str
            The WMTS capabilities URL.
        task : dict
            The preview task that triggered this fetch.
        """
        # Check if already fetching for this provider
        if provider_url in self._pending_capabilities:
            self._pending_capabilities[provider_url].append(task)
            return

        # Start new fetch
        self._pending_capabilities[provider_url] = [task]

        nam = QgsNetworkAccessManager.instance()
        request = QNetworkRequest(QUrl(provider_url))
        request.setHeader(
            QNetworkRequest.UserAgentHeader,
            "Mozilla/5.0 QGIS/3.0.0 Basemaps Plugin",
        )

        reply = nam.get(request)
        reply.finished.connect(
            lambda r=reply, url=provider_url: self._on_capabilities_fetched(r, url)
        )

    def _on_capabilities_fetched(self, reply, provider_url: str) -> None:
        """Handle WMTS capabilities fetch completion.

        Parameters
        ----------
        reply : QNetworkReply
            The network reply object.
        provider_url : str
            The WMTS capabilities URL.
        """
        content = reply.readAll()
        error_code = reply.error()
        reply.deleteLater()

        waiting_tasks = self._pending_capabilities.pop(provider_url, [])

        if error_code != 0 or not content:
            Logger.warning(f"Failed to fetch WMTS capabilities: {provider_url}")
            # Fall back to KVP for all waiting tasks
            for task in waiting_tasks:
                self._request_queue.insert(0, task)
            self._process_queue()
            return

        try:
            # Parse capabilities to extract ResourceURLs
            layers = wmts_parser.parse_wmts_capabilities(bytes(content))

            # Build cache for this provider
            if provider_url not in self._resource_url_cache:
                self._resource_url_cache[provider_url] = {}

            for layer in layers:
                layer_name = layer.get("layer_name", "")
                resource_url = layer.get("resource_url")
                if layer_name and resource_url:
                    self._resource_url_cache[provider_url][layer_name] = resource_url

            Logger.info(
                f"Cached {len(self._resource_url_cache[provider_url])} ResourceURLs for {provider_url}"
            )

            # Re-queue waiting tasks with updated layer_data
            for task in waiting_tasks:
                layer_data = task.get("layer_data")
                if layer_data:
                    layer_name = layer_data.get("layer_name", "")
                    cached_url = self._resource_url_cache.get(provider_url, {}).get(
                        layer_name
                    )
                    if cached_url:
                        layer_data["resource_url"] = cached_url
                self._request_queue.insert(0, task)

            self._process_queue()

        except Exception as e:
            Logger.warning(f"Failed to parse WMTS capabilities: {e}")
            # Fall back to KVP for all waiting tasks
            for task in waiting_tasks:
                self._request_queue.insert(0, task)
            self._process_queue()

    def cleanup(self) -> None:
        """Cancel all pending network requests and clear queues."""
        for reply in list(self._active_requests.values()):
            if reply and not reply.isFinished():
                reply.abort()
                reply.deleteLater()

        for task in list(self._vector_preview_tasks.values()):
            if task:
                task.cancel()

        self._active_requests.clear()
        self._request_queue.clear()
        self._pending_tasks.clear()
        self._active_composites.clear()
        self._wayback_waiting.clear()
        self._pending_capabilities.clear()
        self._vector_preview_tasks.clear()

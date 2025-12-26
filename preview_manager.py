from __future__ import annotations

import os
from pathlib import Path

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QObject, pyqtSignal, QUrl, Qt
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtGui import QImage, QPainter

from .messageTool import Logger
from . import wmts_parser


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
            Logger.info(f"Wayback preview requested: shared_key={shared_key}, first_key={key}")

        # Analyze if we need composite fetch
        is_bing = "{q}" in url
        is_eox = "eox.at" in url.lower() or (
            layer_data and "eox" in provider_name.lower()
        )
        needs_z1_composite = (
            is_bing
            or "f4map.com" in url
            or "opentopomap.org" in url
            or is_eox
            or service_type == "wmts"
        )

        task_type = "composite" if needs_z1_composite else "single"

        task = {
            "type": task_type,
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

        # For WMTS without resource_url, try to get it from cache or fetch capabilities
        if service_type == "wmts" and layer_data:
            resource_url = layer_data.get("resource_url")
            if not resource_url:
                # Check cache first
                layer_name_for_cache = layer_data.get("layer_name", "")
                cached_url = self._resource_url_cache.get(url, {}).get(layer_name_for_cache)
                if cached_url:
                    layer_data["resource_url"] = cached_url
                    Logger.info(f"Using cached ResourceURL for {layer_name_for_cache}")
                else:
                    # Need to fetch capabilities first
                    Logger.info(f"No ResourceURL for {layer_name_for_cache}, fetching capabilities...")
                    self._fetch_capabilities_async(url, task)
                    return

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
                self._active_composites[key] = {
                    "received": {},
                    "total": 4,
                    "failed": False,
                    "task": task,
                }

                tiles = [(0, 0, 0), (1, 0, 1), (0, 1, 2), (1, 1, 3)]

                for x, y, idx in tiles:
                    fetch_url = self._construct_preview_url(
                        task["url"],
                        task["service_type"],
                        task["layer_data"],
                        z=1,
                        x=x,
                        y=y,
                    )
                    if fetch_url:
                        req_id = f"{key}_comp_{idx}"
                        self._start_request(fetch_url, req_id, task, composite_idx=idx)
                    else:
                        self._active_composites[key]["failed"] = True
                        self._on_fetch_failed(task)
                        break

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
            if hasattr(QNR, 'RedirectPolicyAttribute'):
                request.setAttribute(QNR.RedirectPolicyAttribute, 1)  # 1 = NoLessSafeRedirectPolicy
            elif hasattr(QNR, 'FollowRedirectsAttribute'):
                request.setAttribute(QNR.FollowRedirectsAttribute, True)
        except Exception:
            pass

        reply = nam.get(request)
        reply.finished.connect(
            lambda r=reply, rid=req_id, t=task, c_idx=composite_idx: self._on_reply_finished(
                r, rid, t, c_idx
            )
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
            img_format = (
                "image/jpeg"
                if "image/jpeg" in format_list
                else (format_list[0] if format_list else "image/jpeg")
            )
            styles = layer_data.get("styles", ["default"])
            style = styles[0] if styles else "default"

            # Check for ResourceURL template (RESTful WMTS)
            resource_url = layer_data.get("resource_url")
            if resource_url:
                # Build URL from ResourceURL template
                preview_url = resource_url
                preview_url = preview_url.replace("{TileMatrixSet}", tile_matrix_set)
                preview_url = preview_url.replace("{TileMatrix}", str(z))
                preview_url = preview_url.replace("{TileRow}", str(y))
                preview_url = preview_url.replace("{TileCol}", str(x))
                preview_url = preview_url.replace("{Style}", style)
                preview_url = preview_url.replace("{style}", style)
                Logger.info(f"WMTS preview URL (ResourceURL): {preview_url}")
                return preview_url

            # Fallback to KVP (Key-Value Pair) style URL
            base_url = url.replace("/WMTSCapabilities.xml", "").replace(
                "WMTSCapabilities.xml", ""
            )
            if base_url.endswith("/1.0.0"):
                base_url = base_url[:-6]

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
                Logger.info(f"WMTS tile fetch OK - key: {key}, idx: {composite_idx}, size: {len(content)}")
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
                Logger.warning(f"Composite tile {composite_idx} failed for {key}")
                comp_data["failed"] = True

            if comp_data.get("failed", False):
                Logger.warning(f"Composite preview failed for {key}")
                self._on_fetch_failed(task)
                self._active_composites.pop(key, None)
            elif len(comp_data["received"]) == comp_data["total"]:
                self._merge_and_save(task, comp_data["received"])
                self._active_composites.pop(key, None)

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

        final_img = canvas.scaled(256, 256, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

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
                Logger.info(f"Wayback preview saved to {path}, emitting for {len(waiting_keys)} keys")
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
                    cached_url = self._resource_url_cache.get(provider_url, {}).get(layer_name)
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

        self._active_requests.clear()
        self._request_queue.clear()
        self._pending_tasks.clear()
        self._active_composites.clear()
        self._wayback_waiting.clear()
        self._pending_capabilities.clear()

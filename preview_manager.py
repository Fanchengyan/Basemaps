from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from qgis.core import QgsNetworkAccessManager, QgsNetworkReplyContent
from qgis.PyQt.QtCore import QObject, pyqtSignal, QUrl, QRect
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtGui import QImage, QPainter

from .messageTool import Logger

class PreviewManager(QObject):
    """Manager for fetching and caching basemap preview tiles."""
    
    preview_readied = pyqtSignal(str, str)  # key, image_path

    def __init__(self, resources_dir: Path):
        super().__init__()
        self.resources_dir = resources_dir
        self.previews_dir = resources_dir / "previews"
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        # Create separate subdirectories for xyz and wms
        self.xyz_previews_dir = self.previews_dir / "xyz"
        self.wms_previews_dir = self.previews_dir / "wms"
        self.xyz_previews_dir.mkdir(parents=True, exist_ok=True)
        self.wms_previews_dir.mkdir(parents=True, exist_ok=True)
        self.failed_icon_path = resources_dir / "icons" / "error.svg"
        self._pending_tasks = set()
        self._active_requests = {}  # Map of request_id -> reply object
        self._request_queue = []  # Queue of task tuples
        
        # Track composite downloads: key -> {'received': {index: QImage}, 'total': 4, 'failed': bool}
        self._active_composites = {} 

        import os
        cpu_count = os.cpu_count() or 2
        if cpu_count <= 4:
            self._max_concurrent = 2
        else:
            self._max_concurrent = (cpu_count // 2) + 1
        self._max_concurrent = min(self._max_concurrent, 8)

    def get_preview_path(self, provider_name: str, layer_name: str, service_type: str = "xyz") -> Path:
        """Get the cached preview path for a given provider and layer."""
        base_dir = self.wms_previews_dir if service_type in ["wms", "wmts"] else self.xyz_previews_dir
        safe_provider = "".join([c for c in provider_name if c.isalnum()])
        safe_layer = "".join([c for c in layer_name if c.isalnum()])
        filename = f"{safe_provider}_{safe_layer}.png"
        return base_dir / filename

    def request_preview(self, provider_name: str, layer_name: str, url: str, service_type: str = "xyz", layer_data: dict | None = None):
        """Request a preview, fetching from network if not cached."""
        preview_path = self.get_preview_path(provider_name, layer_name, service_type)
        key = f"{provider_name}_{layer_name}"

        if preview_path.exists():
            self.preview_readied.emit(key, str(preview_path))
            return

        if key in self._pending_tasks:
            return

        self._pending_tasks.add(key)
        
        # Analyze if we need composite fetch immediately (e.g. Bing)
        # Bing needs 4 tiles (0,1,2,3) to make a global map at z=1
        is_bing = "{q}" in url
        # Known providers that fail at z=0 or return blank tiles
        # - Bing: requires quadkeys
        # - F4map, OpenTopoMap: no z=0 tiles
        # - EOX: z=0 returns blank images
        is_eox = "eox.at" in url.lower() or (layer_data and "eox" in provider_name.lower())
        needs_z1_composite = is_bing or "f4map.com" in url or "opentopomap.org" in url or is_eox or service_type == "wmts"
        
        task_type = "composite" if needs_z1_composite else "single"
        
        self._request_queue.append({
            'type': task_type,
            'provider': provider_name,
            'layer': layer_name,
            'url': url,
            'service_type': service_type,
            'layer_data': layer_data,
            'path': preview_path,
            'key': key,
            'retry_as_composite': True # If single fails, retry as composite
        })
        self._process_queue()
    
    def _process_queue(self):
        """Process queued requests up to max concurrent limit."""
        while len(self._active_requests) < self._max_concurrent and self._request_queue:
            task = self._request_queue.pop(0)
            key = task['key']
            
            if task['type'] == 'single':
                # Try z=0 single tile
                fetch_url = self._construct_preview_url(task['url'], task['service_type'], task['layer_data'], z=0, x=0, y=0)
                if not fetch_url:
                    self._on_fetch_failed(key)
                    continue
                
                req_id = f"{key}_single"
                self._start_request(fetch_url, req_id, task)
                
            elif task['type'] == 'composite':
                # Start 4 requests for z=1 (2x2 grid)
                # Note: Some CRS like WGS84 may have different tile counts, but most servers
                # will return valid tiles or empty tiles for out-of-range requests
                self._active_composites[key] = {'received': {}, 'total': 4, 'failed': False, 'task': task}
                
                # Tiles for z=1: (x, y, index)
                # 0: top-left, 1: top-right, 2: bottom-left, 3: bottom-right
                tiles = [
                    (0, 0, 0), (1, 0, 1),
                    (0, 1, 2), (1, 1, 3)
                ]
                
                for x, y, idx in tiles:
                    fetch_url = self._construct_preview_url(task['url'], task['service_type'], task['layer_data'], z=1, x=x, y=y)
                    if fetch_url:
                        req_id = f"{key}_comp_{idx}"
                        self._start_request(fetch_url, req_id, task, composite_idx=idx)
                    else:
                        # If URL construction fails, mark failed
                        self._active_composites[key]['failed'] = True
                        self._on_fetch_failed(key)
                        break

    def _start_request(self, url: str, req_id: str, task: dict, composite_idx: int = -1):
        nam = QgsNetworkAccessManager.instance()
        request = QNetworkRequest(QUrl(url))
        
        # Add User-Agent to avoid blocking (e.g. OpenTopoMap, Wayback)
        request.setHeader(QNetworkRequest.UserAgentHeader, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) QGIS/3.0.0 Safari/537.36")
        
        reply = nam.get(request)
        # We need to pass task info to the callback
        reply.finished.connect(lambda r=reply, rid=req_id, t=task, c_idx=composite_idx: self._on_reply_finished(r, rid, t, c_idx))
        self._active_requests[req_id] = reply

    def _construct_preview_url(self, url: str, service_type: str, layer_data: dict | None = None, z: int=0, x: int=0, y: int=0) -> str | None:
        """Construct a valid URL for a tile at given coordinates."""
        if service_type == "xyz":
            # Handle Bing quadkey
            if "{q}" in url:
                # Convert z,x,y to quadkey
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
            
            # Standard XYZ
            preview_url = url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            preview_url = preview_url.replace("{-y}", str(y)) # Simplification: assume 0,0 etc works, usually {-y} needs calculation but for z=0/1 it's simple enough or we'd need max_y.
            # actually for z=1, max y is 1. inverted y: pow(2,z) - 1 - y.
            # z=1: max=1. if y=0, inv=1. if y=1, inv=0.
            if "{-y}" in url:
                max_y = (1 << z) - 1
                inv_y = max_y - y
                preview_url = url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{-y}", str(inv_y))

            # Handle switch
            import re
            switch_pattern = r'\{switch:([^}]+)\}'
            match = re.search(switch_pattern, preview_url)
            if match:
                options = match.group(1).split(',')
                preview_url = preview_url.replace(match.group(0), options[0])
            
            return preview_url
            
        elif service_type == "wms":
            # WMS GetMap - still just requests one big image usually
            # But if we are doing composite, WMS params need to adjust BBOX
            # For simplicity, if z=0 requested, give global bbox.
            # If z=1 requested, we need quadrant bboxes.
            
            if not layer_data: return None
            layer_name = layer_data.get("layer_name", "")
            if not layer_name: return None
            
            crs_list = layer_data.get("crs", [])
            # Prefer EPSG:3857 for uniformity with tiles if possible
            crs = "EPSG:3857" if "EPSG:3857" in crs_list else ("EPSG:4326" if "EPSG:4326" in crs_list else crs_list[0])
            
            format_list = layer_data.get("format", [])
            img_format = "image/png" if "image/png" in format_list else (format_list[0] if format_list else "image/png")
            
            # Calculate BBOX
            # World Extents:
            # 4326: -180,-90, 180, 90
            # 3857: -20037508.34, -20037508.34, 20037508.34, 20037508.34
            
            min_x, min_y, max_x, max_y = -180, -90, 180, 90
            if crs == "EPSG:3857":
                min_x, min_y, max_x, max_y = -20037508.34, -20037508.34, 20037508.34, 20037508.34
                
            if z == 1:
                mid_x = (min_x + max_x) / 2
                mid_y = (min_y + max_y) / 2
                # 0: top-left (min_x, mid_y, mid_x, max_y)
                # 1: top-right (mid_x, mid_y, max_x, max_y)
                # 2: bottom-left (min_x, min_y, mid_x, mid_y)
                # 3: bottom-right (mid_x, min_y, max_x, mid_y)
                # Mapping from tile (x,y) to quadrant needs to match index used in _process_queue
                # x=0, y=0 (top-left) -> ? In TMS/XYZ usually y goes down?
                # XYZ: y=0 is top. WMS BBOX: min_y is bottom.
                # So tile y=0 (top) corresponds to upper half (mid_y to max_y)
                
                if x == 0 and y == 0: # Top-Left
                    bbox = f"{min_x},{mid_y},{mid_x},{max_y}"
                elif x == 1 and y == 0: # Top-Right
                    bbox = f"{mid_x},{mid_y},{max_x},{max_y}"
                elif x == 0 and y == 1: # Bottom-Left
                    bbox = f"{min_x},{min_y},{mid_x},{mid_y}"
                elif x == 1 and y == 1: # Bottom-Right
                    bbox = f"{mid_x},{min_y},{max_x},{mid_y}"
                else: 
                     bbox = f"{min_x},{min_y},{max_x},{max_y}"
            else:
                 bbox = f"{min_x},{min_y},{max_x},{max_y}"

            params = {
                "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                "LAYERS": layer_name, "CRS": crs, "BBOX": bbox,
                "WIDTH": "256", "HEIGHT": "256", "FORMAT": img_format,
                "STYLES": layer_data.get("styles", [""])[0] or ""
            }
            
            from urllib.parse import urlencode
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}{urlencode(params)}"

        elif service_type == "wmts":
            if not layer_data: return None
            layer_name = layer_data.get("layer_name", "")
            if not layer_name: return None
            
            crs_list = layer_data.get("crs", [])
            if not crs_list:
                return None
            
            # Select tile matrix set - prefer GoogleMapsCompatible, then 'g', then first available
            if "GoogleMapsCompatible" in crs_list:
                tile_matrix_set = "GoogleMapsCompatible"
            elif "g" in crs_list:
                tile_matrix_set = "g"
            else:
                tile_matrix_set = crs_list[0]
            
            format_list = layer_data.get("format", [])
            img_format = "image/jpeg" if "image/jpeg" in format_list else (format_list[0] if format_list else "image/jpeg")
            styles = layer_data.get("styles", ["default"])
            style = styles[0] if styles else "default"
            
            base_url = url.replace("/WMTSCapabilities.xml", "").replace("WMTSCapabilities.xml", "")
            # Also strip /1.0.0 if present at the end
            if base_url.endswith("/1.0.0"):
                base_url = base_url[:-6]
            
            # Use RESTful construction for typical tile servers
            ext = "png" if "png" in img_format else "jpg"
            
            if "wayback" in base_url.lower():
                # ArcGIS Wayback specific pattern
                # Base should be: https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer
                # Need: /WMTS/tile/1.0.0/LAYER/STYLE/TMS/Z/ROW/COL
                if "/WMTS" not in base_url:
                    base_url = base_url.rstrip("/") + "/WMTS"
                if "/tile" not in base_url:
                    base_url = base_url.rstrip("/") + "/tile"
                
                return f"{base_url}/1.0.0/{layer_name}/{style}/{tile_matrix_set}/{z}/{y}/{x}"
            
            elif "eox.at" in base_url.lower():
                # EOX: https://tiles.maps.eox.at/wmts/1.0.0/LAYER/STYLE/TMS/Z/ROW/COL.ext
                return f"{base_url}/1.0.0/{layer_name}/{style}/{tile_matrix_set}/{z}/{y}/{x}.{ext}"
            
            else:
                # Fallback to KVP
                params = {
                    "SERVICE": "WMTS", "REQUEST": "GetTile", "VERSION": "1.0.0",
                    "LAYER": layer_name, "STYLE": style, "TILEMATRIXSET": tile_matrix_set,
                    "TILEMATRIX": str(z), "TILEROW": str(y), "TILECOL": str(x),
                    "FORMAT": img_format,
                }
                from urllib.parse import urlencode
                separator = "&" if "?" in base_url else "?"
                return f"{base_url}{separator}{urlencode(params)}"
        
        return None

    def _on_reply_finished(self, reply, req_id: str, task: dict, composite_idx: int):
        content = reply.readAll()
        reply.deleteLater()
        self._active_requests.pop(req_id, None)
        
        key = task['key']
        
        # Check success
        success = False
        image = QImage()
        if content and len(content) > 50:
             image.loadFromData(content)
             if not image.isNull() and image.width() > 10:
                 success = True

        if task['type'] == 'single':
            if success:
                # Success!
                self._finalize_image(key, image, task['path'])
            elif task.get('retry_as_composite', False):
                # Failed single, retry as composite
                Logger.info(f"Preview z=0 failed for {key}, retrying as composite z=1")
                task['type'] = 'composite'
                task['retry_as_composite'] = False
                # Push back to front of queue
                self._request_queue.insert(0, task)
                self._process_queue()
            else:
                Logger.warning(f"Preview failed for {key}")
                self._on_fetch_failed(key)
                
        elif task['type'] == 'composite':
            comp_data = self._active_composites.get(key)
            if not comp_data: return # Already failed/finished?
            
            if success:
                comp_data['received'][composite_idx] = image
            else:
                # One tile failed - mark as failed
                Logger.warning(f"Composite tile {composite_idx} failed for {key}")
                comp_data['failed'] = True
                
            # Check if all received or if any failed
            if comp_data.get('failed', False):
                # At least one tile failed, fail the whole preview
                Logger.warning(f"Composite preview failed for {key}")
                self._on_fetch_failed(key)
                self._active_composites.pop(key, None)
            elif len(comp_data['received']) == comp_data['total']:
                # All tiles received successfully - merge
                self._merge_and_save(key, comp_data['received'], task['path'])
                self._active_composites.pop(key, None)
            
            # Trigger queue processing to keep slots full
            self._process_queue()

    def _merge_and_save(self, key: str, images: dict, path: Path):
        # Create 512x512 canvas for 4 tiles in 2x2 grid
        canvas = QImage(512, 512, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(0) # Transparent
        painter = QPainter(canvas)
        
        # Tile positions: (x, y) -> canvas position
        # 0: top-left, 1: top-right, 2: bottom-left, 3: bottom-right
        positions = {
            0: (0, 0),
            1: (256, 0),
            2: (0, 256),
            3: (256, 256)
        }
        
        for idx, img in images.items():
            if img and not img.isNull():
                x, y = positions.get(idx, (0, 0))
                painter.drawImage(x, y, img)
        
        painter.end()
        
        # Scale down to 256x256 for preview
        from qgis.PyQt.QtCore import Qt
        final_img = canvas.scaled(256, 256, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        
        if final_img.save(str(path)):
            self._pending_tasks.discard(key)
            self.preview_readied.emit(key, str(path))
            Logger.info(f"Composite preview saved for {key}")
        else:
            self._on_fetch_failed(key)

    def _finalize_image(self, key: str, image: QImage, path: Path):
        if image.save(str(path)):
            self._pending_tasks.discard(key)
            self.preview_readied.emit(key, str(path))
        else:
            self._on_fetch_failed(key)
            
        self._process_queue()

    def _on_fetch_failed(self, key: str):
        self._pending_tasks.discard(key)
        self.preview_readied.emit(key, str(self.failed_icon_path))
        self._active_composites.pop(key, None) # Cleanup if composite
        self._process_queue()

    def cleanup(self):
        """Cancel all pending network requests and clear queues."""
        # Create list copy to avoid "dictionary changed size during iteration" error
        for reply in list(self._active_requests.values()):
            if reply and not reply.isFinished():
                reply.abort()
                reply.deleteLater()
        
        self._active_requests.clear()
        self._request_queue.clear()
        self._pending_tasks.clear()
        self._active_composites.clear()

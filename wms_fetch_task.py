# Copyright (C) 2025  Chengyan (Fancy) Fan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""WMS/WMTS layer fetching task for background processing.

This module provides QgsTask subclasses for fetching WMS/WMTS service
capabilities without blocking the QGIS UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal

import requests
from owslib.wms import WebMapService
from owslib.wmts import WebMapTileService
from qgis.core import QgsTask
from qgis.PyQt.QtCore import QObject, pyqtSignal

from . import wmts_parser
from .messageTool import Logger

if TYPE_CHECKING:
    pass


class ServiceType(Enum):
    """Enumeration of supported OGC service types."""

    WMS = "wms"
    WMTS = "wmts"
    UNKNOWN = "unknown"


@dataclass
class FetchResult:
    """Result container for WMS/WMTS fetch operations.

    Attributes
    ----------
    success : bool
        Whether the fetch operation succeeded.
    layers : list[dict[str, Any]]
        List of layer dictionaries if successful, empty list otherwise.
    service_type : ServiceType
        The detected service type.
    error_message : str
        Error message if failed, empty string otherwise.
    url : str
        The URL that was fetched.
    """

    success: bool
    layers: list[dict[str, Any]]
    service_type: ServiceType
    error_message: str
    url: str


class WMSFetchSignals(QObject):
    """Signal container for WMS fetch task.

    Signals must be defined in a QObject subclass because QgsTask
    does not directly inherit from QObject in a way that supports
    custom signal definitions.

    Attributes
    ----------
    finished : pyqtSignal
        Emitted when task completes with FetchResult.
    progress_updated : pyqtSignal
        Emitted when progress changes with message string.
    """

    finished = pyqtSignal(object)  # FetchResult
    progress_updated = pyqtSignal(str)  # Progress message


class WMSFetchTask(QgsTask):
    """Background task for fetching WMS/WMTS service capabilities.

    This task runs HTTP requests in a background thread to avoid
    blocking the QGIS UI. Results are communicated back to the main
    thread via signals.

    Parameters
    ----------
    url : str
        The WMS/WMTS service URL to fetch.
    service_type_hint : Literal["wms", "wmts", "auto"]
        Hint for the service type. If "auto", will try to detect.
        Defaults to "auto".
    timeout : int
        HTTP request timeout in seconds. Defaults to 30.

    Attributes
    ----------
    signals : WMSFetchSignals
        Signal container for communicating results.

    Examples
    --------
    >>> from qgis.core import QgsApplication
    >>> task = WMSFetchTask("https://example.com/wms", timeout=30)
    >>> task.signals.finished.connect(on_fetch_complete)
    >>> QgsApplication.taskManager().addTask(task)
    """

    def __init__(
        self,
        url: str,
        service_type_hint: Literal["wms", "wmts", "auto"] = "auto",
        timeout: int = 30,
    ) -> None:
        super().__init__(f"Fetching layers from {url}", QgsTask.CanCancel)
        self.url = url
        self.service_type_hint = service_type_hint
        self.timeout = timeout
        self.signals = WMSFetchSignals()

        # Result storage (set during run())
        self._result: FetchResult | None = None

    def run(self) -> bool:
        """Execute the fetch operation in background thread.

        This method is called by QgsTaskManager in a worker thread.
        It should NOT interact with any GUI elements or QObjects
        that live on the main thread.

        Returns
        -------
        bool
            True if task completed successfully, False otherwise.

        Notes
        -----
        Do not emit signals with GUI objects from this method.
        Use setProgress() for progress reporting.
        """
        try:
            self.setProgress(10)

            # Check for cancellation before starting network request
            if self.isCanceled():
                return False

            # Detect service type
            detected_type = self._detect_service_type(self.url)
            if self.service_type_hint != "auto":
                detected_type = ServiceType(self.service_type_hint)

            self.setProgress(20)

            if self.isCanceled():
                return False

            # Fetch layers based on service type
            layers: list[dict[str, Any]] = []
            final_type = detected_type

            if detected_type == ServiceType.WMTS:
                try:
                    layers, final_type = self._fetch_wmts_layers()
                except Exception as e:
                    Logger.warning(f"WMTS parsing failed, trying WMS: {e}")
                    if not self.isCanceled():
                        layers, final_type = self._fetch_wms_layers()
            elif detected_type == ServiceType.WMS:
                try:
                    layers, final_type = self._fetch_wms_layers()
                except Exception as e:
                    Logger.warning(f"WMS parsing failed, trying WMTS: {e}")
                    if not self.isCanceled():
                        layers, final_type = self._fetch_wmts_layers()
            else:
                # Unknown type, try WMTS first then WMS
                try:
                    layers, final_type = self._fetch_wmts_layers()
                except Exception:
                    if not self.isCanceled():
                        layers, final_type = self._fetch_wms_layers()

            self.setProgress(90)

            if self.isCanceled():
                return False

            # Sort layers by name
            layers.sort(key=lambda x: x.get("layer_name", "").lower())

            self._result = FetchResult(
                success=True,
                layers=layers,
                service_type=final_type,
                error_message="",
                url=self.url,
            )

            self.setProgress(100)
            return True

        except Exception as e:
            Logger.critical(f"Failed to fetch layers: {e}")
            self._result = FetchResult(
                success=False,
                layers=[],
                service_type=ServiceType.UNKNOWN,
                error_message=str(e),
                url=self.url,
            )
            return False

    def finished(self, result: bool) -> None:
        """Handle task completion on the main thread.

        This method is called by QgsTaskManager on the main thread
        after run() completes. It's safe to interact with GUI here.

        Parameters
        ----------
        result : bool
            The return value from run().
        """
        if self._result is None:
            self._result = FetchResult(
                success=False,
                layers=[],
                service_type=ServiceType.UNKNOWN,
                error_message="Task was cancelled or failed unexpectedly",
                url=self.url,
            )

        self.signals.finished.emit(self._result)

    def cancel(self) -> None:
        """Request task cancellation.

        Overrides QgsTask.cancel() to add logging.
        """
        Logger.info(f"Cancelling fetch task for {self.url}", notify_user=False)
        super().cancel()

    def _detect_service_type(self, url: str) -> ServiceType:
        """Detect service type from URL.

        Parameters
        ----------
        url : str
            The service URL.

        Returns
        -------
        ServiceType
            The detected service type.
        """
        url_lower = url.lower()
        if "wmts" in url_lower or "wmtscapabilities" in url_lower:
            return ServiceType.WMTS
        if "wms" in url_lower:
            return ServiceType.WMS
        return ServiceType.UNKNOWN

    def _fetch_wmts_layers(self) -> tuple[list[dict], ServiceType]:
        """Fetch layers from WMTS service.

        Returns
        -------
        tuple[list[dict], ServiceType]
            Tuple of (layers list, service type).

        Raises
        ------
        Exception
            If both OWSLib and ElementTree parsing fail.
        """
        self.setProgress(40)

        # Try OWSLib first
        try:
            return self._fetch_wmts_with_owslib()
        except Exception as e:
            Logger.warning(f"OWSLib WMTS parsing failed: {e}")

        if self.isCanceled():
            raise Exception("Task cancelled")

        self.setProgress(60)

        # Fallback to ElementTree
        return self._fetch_wmts_with_elementtree()

    def _fetch_wmts_with_owslib(self) -> tuple[list[dict], ServiceType]:
        """Fetch WMTS layers using OWSLib.

        Returns
        -------
        tuple[list[dict], ServiceType]
            Tuple of (layers list, ServiceType.WMTS).
        """
        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        xml_content = response.text

        # Fix namespace issues
        xml_fixed = self._fix_wmts_namespaces(xml_content)
        wmts = WebMapTileService(self.url, xml=BytesIO(xml_fixed.encode("utf-8")))

        layers = []
        for layer_name, layer in wmts.contents.items():
            tile_matrix_sets = []
            if hasattr(layer, "tilematrixsetlinks"):
                tile_matrix_sets = list(layer.tilematrixsetlinks.keys())

            formats = []
            if hasattr(layer, "formats") and layer.formats:
                formats = list(layer.formats)
            if not formats:
                formats = ["image/jpeg"]

            styles = []
            if hasattr(layer, "styles") and layer.styles:
                styles = list(layer.styles.keys())

            layer_info = {
                "layer_name": layer_name,
                "layer_title": (
                    layer.title if hasattr(layer, "title") and layer.title else layer_name
                ),
                "crs": tile_matrix_sets,
                "format": formats,
                "styles": styles,
                "service_type": "wmts",
            }
            layers.append(layer_info)

        return layers, ServiceType.WMTS

    def _fetch_wmts_with_elementtree(self) -> tuple[list[dict], ServiceType]:
        """Fetch WMTS layers using ElementTree fallback.

        Returns
        -------
        tuple[list[dict], ServiceType]
            Tuple of (layers list, ServiceType.WMTS).
        """
        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()

        layers = wmts_parser.parse_wmts_capabilities(response.content)
        return layers, ServiceType.WMTS

    def _fetch_wms_layers(self) -> tuple[list[dict], ServiceType]:
        """Fetch layers from WMS service.

        Returns
        -------
        tuple[list[dict], ServiceType]
            Tuple of (layers list, ServiceType.WMS).
        """
        self.setProgress(50)

        wms = WebMapService(self.url, timeout=self.timeout)

        layers = []
        for layer_name, layer in wms.contents.items():
            layer_info = {
                "layer_name": layer_name,
                "layer_title": layer.title,
                "crs": [str(crs) for crs in layer.crsOptions],
                "format": wms.getOperationByName("GetMap").formatOptions,
                "styles": [style.get("name", "") for style in layer.styles.values()],
                "service_type": "wms",
            }
            layers.append(layer_info)

        return layers, ServiceType.WMS

    @staticmethod
    def _fix_wmts_namespaces(xml_content: str) -> str:
        """Fix non-standard WMTS XML namespace URIs.

        Some WMTS services (like ArcGIS) use HTTPS in namespace URIs,
        which OWSLib doesn't handle correctly.

        Parameters
        ----------
        xml_content : str
            The WMTS capabilities XML content.

        Returns
        -------
        str
            The XML with fixed namespace URIs.
        """
        replacements = [
            ('xmlns="https://www.opengis.net/', 'xmlns="http://www.opengis.net/'),
            ('xmlns:ows="https://www.opengis.net/', 'xmlns:ows="http://www.opengis.net/'),
            ('xmlns:xlink="https://www.w3.org/', 'xmlns:xlink="http://www.w3.org/'),
            ('xmlns:gml="https://www.opengis.net/', 'xmlns:gml="http://www.opengis.net/'),
            (
                'xsi:schemaLocation="https://www.opengis.net/',
                'xsi:schemaLocation="http://www.opengis.net/',
            ),
            ("https://schemas.opengis.net/", "http://schemas.opengis.net/"),
        ]
        for old, new in replacements:
            xml_content = xml_content.replace(old, new)
        return xml_content

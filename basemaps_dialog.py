# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

from __future__ import annotations

import os
import tempfile
from pathlib import Path
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
from qgis.PyQt.QtCore import (
    QT_VERSION_STR,
    QCoreApplication,
    QModelIndex,
    QSettings,
    QSize,
    Qt,
    QUrl,
)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolTip,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import config_loader
from .messageTool import Logger, MessageBox
from .preview_manager import PreviewManager
from .ui import IconBasemaps, UIBasemapsBase
from .ui.basemap_delegate import TAG_COLORS, BasemapCardDelegate
from .wms_fetch_task import FetchResult, WMSFetchTask

QT_VERSION_INT = int(QT_VERSION_STR.split(".")[0])

if QT_VERSION_INT <= 5:
    extended_selection = QAbstractItemView.ExtendedSelection
    custom_context_menu = Qt.CustomContextMenu
    user_role = Qt.UserRole
    item_enabled = Qt.ItemIsEnabled
    item_selectable = Qt.ItemIsSelectable
    button_ok = QDialogButtonBox.Ok
    button_cancel = QDialogButtonBox.Cancel
    dialog_accepted = QDialog.Accepted
    window_modal = Qt.WindowModal
    http_status_code_attribute = QNetworkRequest.HttpStatusCodeAttribute
else:
    extended_selection = QAbstractItemView.SelectionMode.ExtendedSelection
    custom_context_menu = Qt.ContextMenuPolicy.CustomContextMenu
    user_role = Qt.ItemDataRole.UserRole
    item_enabled = Qt.ItemFlag.ItemIsEnabled
    item_selectable = Qt.ItemFlag.ItemIsSelectable
    button_ok = QDialogButtonBox.StandardButton.Ok
    button_cancel = QDialogButtonBox.StandardButton.Cancel
    dialog_accepted = QDialog.DialogCode.Accepted
    window_modal = Qt.WindowModality.WindowModal
    http_status_code_attribute = QNetworkRequest.Attribute.HttpStatusCodeAttribute

VECTOR_STYLE_REQUEST_HEADERS = (
    (
        b"User-Agent",
        b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        b"AppleWebKit/537.36 (KHTML, like Gecko) "
        b"Chrome/120.0.0.0 Safari/537.36",
    ),
    (b"Accept", b"application/json, text/plain, */*"),
)

default_separator = {
    "name": "Default Providers ─────────────────",
    "type": "separator",
}

user_separator = {
    "name": "User Providers ──────────────────",
    "type": "separator",
}

OVERLAY_TAG = "Overlay"
OVERLAY_TAG_PREFIX = f"{OVERLAY_TAG}/"

TAG_SORT_ORDER = [
    "Satellite",
    "Streets",
    "Terrain",
    "Thematic",
    "Overlay/Labels",
    "Overlay/Boundaries",
    "Overlay/Transportation",
    "Overlay/Hydrography",
    OVERLAY_TAG,
]

AVAILABLE_TAGS = [
    "All",
    "Satellite",
    "Streets",
    "Terrain",
    "Thematic",
    OVERLAY_TAG,
    "Overlay/Hydrography",
    "Overlay/Transportation",
    "Overlay/Labels",
    "Overlay/Boundaries",
]

ASSIGNABLE_TAGS = [
    "Satellite",
    "Streets",
    "Terrain",
    "Thematic",
    OVERLAY_TAG,
    "Overlay/Hydrography",
    "Overlay/Transportation",
    "Overlay/Labels",
    "Overlay/Boundaries",
]

TOKEN_PARAM_OPTIONS = ["apikey", "key", "api_key", "access_token", "token", "tk"]
DEFAULT_TOKEN_PARAM = TOKEN_PARAM_OPTIONS[0]

WELL_KNOWN_XYZ_PROVIDERS = {
    "MapTiler": {"icon": "MapTiler.svg", "token_param": "key"},
    "Mapbox": {"icon": "", "token_param": "access_token"},
    "Thunderforest": {"icon": "", "token_param": "apikey"},
    "Stadia Maps": {"icon": "", "token_param": "api_key"},
    "Jawg": {"icon": "", "token_param": "access-token"},
    "TomTom": {"icon": "", "token_param": "key"},
    "HERE": {"icon": "", "token_param": "apiKey"},
    "OpenRouteService": {"icon": "", "token_param": "api_key"},
}


def _run_qt_menu(menu: QMenu, global_position: Any) -> Any:
    """Show a Qt menu with PyQt5/PyQt6 compatibility.

    Parameters
    ----------
    menu : QMenu
        Context menu to show.
    global_position : Any
        Global screen position returned by ``mapToGlobal``.

    Returns
    -------
    Any
        The selected QAction, or ``None`` when the menu is dismissed.
    """
    show_menu = getattr(menu, "exec_", None)
    if show_menu is None:
        show_menu = getattr(menu, "exec")
    return show_menu(global_position)


def _run_qt_dialog(dialog: QDialog) -> Any:
    """Run a modal Qt dialog with PyQt5/PyQt6 compatibility.

    Parameters
    ----------
    dialog : QDialog
        Dialog to run modally.

    Returns
    -------
    Any
        Dialog result code.
    """
    show_dialog = getattr(dialog, "exec_", None)
    if show_dialog is None:
        show_dialog = getattr(dialog, "exec")
    return show_dialog()


class VectorTileLoadTask(QgsTask):
    """Background task for loading a vector tile layer with a remote style URL.

    Downloads the style JSON in a background thread so the UI stays
    responsive, then creates and styles the layer on the main thread.
    """

    def __init__(self, encoded_uri: str, name: str, style_url: str) -> None:
        super().__init__(
            QCoreApplication.translate(
                "BasemapsDialog", "Loading vector tile basemap..."
            ),
            QgsTask.Flag.CanCancel,
        )
        self.encoded_uri = encoded_uri
        self.name = name
        self.style_url = style_url
        self.temp_style_path: str | None = None

    def run(self) -> bool:
        """Download the remote style JSON through QGIS network access.

        Returns
        -------
        bool
            Always ``True`` so layer creation can continue with a remote
            ``styleUrl`` fallback when local style download fails.
        """
        if not self.style_url:
            return True

        style_text = self._fetch_style_text()
        if style_text is None:
            return True

        try:
            fd, self.temp_style_path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(style_text)
        except Exception as error:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog",
                    "Failed to write vector tile style '{}': {}",
                ).format(self.style_url, error)
            )
        return True

    def _fetch_style_text(self) -> str | None:
        """Fetch style JSON text using QGIS network settings.

        Returns
        -------
        str | None
            Response body text when the request succeeds, otherwise ``None``.
        """
        request = QNetworkRequest(QUrl(self.style_url))
        for header, value in VECTOR_STYLE_REQUEST_HEADERS:
            request.setRawHeader(header, value)

        network_request = QgsBlockingNetworkRequest()
        error_code = network_request.get(request, True)
        if error_code != QgsBlockingNetworkRequest.NoError:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog",
                    "Failed to download vector tile style '{}': {}",
                ).format(self.style_url, network_request.errorMessage())
            )
            return None

        reply = network_request.reply()
        status_code = reply.attribute(http_status_code_attribute)
        if status_code and int(status_code) >= 400:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog",
                    "Failed to download vector tile style '{}': HTTP {}",
                ).format(self.style_url, status_code)
            )
            return None

        content = bytes(reply.content())
        if not content:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog",
                    "Vector tile style '{}' returned an empty response",
                ).format(self.style_url)
            )
            return None

        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog",
                    "Failed to decode vector tile style '{}': {}",
                ).format(self.style_url, error)
            )
            return None

    def finished(self, result: bool) -> None:
        """Create the vector tile layer after the background task finishes.

        Parameters
        ----------
        result : bool
            Task result emitted by QGIS. The layer creation path uses the
            downloaded style file when available, or the remote style URL as
            a fallback.
        """
        uri = self.encoded_uri
        if self.temp_style_path:
            uri += f"&styleUrl=file://{self.temp_style_path}"
        else:
            # Ensure styleUrl is present so QGIS loads the style during rendering
            if self.style_url:
                uri += f"&styleUrl={self.style_url}"

        layer = QgsVectorTileLayer(uri, self.name)
        if layer.isValid():
            if self.temp_style_path:
                # Style was pre-downloaded — loadDefaultStyle reads from
                # the local file:// URL and returns quickly
                layer.loadDefaultStyle()
            QgsProject.instance().addMapLayer(layer)
        else:
            MessageBox.critical(
                QCoreApplication.translate(
                    "BasemapsDialog", "Failed to load vector tile layer: {}"
                ).format(self.name),
                QCoreApplication.translate("BasemapsDialog", "Error"),
                None,
            )


class BasemapsDialog(QDialog, UIBasemapsBase):
    def __init__(self, iface, parent=None):
        super(BasemapsDialog, self).__init__(parent)
        self.iface = iface
        self.setupUi(self)
        self.providers_data = []
        self.resources_dir = Path(__file__).parent / "resources"
        self.icons_dir = self.resources_dir / "icons"

        # Task management for async WMS/WMTS fetching
        self._current_fetch_task: WMSFetchTask | None = None
        self._pending_fetch_context: dict | None = None

        # Hold references to vector tile load tasks to prevent GC
        self._vector_tile_tasks: list[VectorTileLoadTask] = []

        # set all list to multiple selection mode

        self.listProviders.setSelectionMode(extended_selection)
        self.listBasemaps.setSelectionMode(extended_selection)
        self.listBasemapsGrid.setSelectionMode(extended_selection)
        self.listWmsProviders.setSelectionMode(extended_selection)
        self.treeWmsLayers.setSelectionMode(extended_selection)
        self.listWmsLayersGrid.setSelectionMode(extended_selection)

        # Initialize Preview Manager
        self.preview_manager = PreviewManager(self.resources_dir)
        self.preview_manager.preview_readied.connect(self._on_preview_ready)

        # Set up Grid Views
        self.xyz_grid_delegate = BasemapCardDelegate(self)
        self.wms_grid_delegate = BasemapCardDelegate(self)
        for grid_view, delegate in [
            (self.listBasemapsGrid, self.xyz_grid_delegate),
            (self.listWmsLayersGrid, self.wms_grid_delegate),
        ]:
            grid_view.setItemDelegate(delegate)
            grid_view.setViewMode(QListView.ViewMode.IconMode)
            grid_view.setResizeMode(QListView.ResizeMode.Adjust)
            grid_view.setWrapping(True)
            grid_view.setSpacing(10)
            grid_view.setWordWrap(True)
            grid_view.setMovement(QListView.Movement.Static)
            grid_view.setMouseTracking(True)
        self.xyz_grid_delegate.tagBadgeClicked.connect(self._on_xyz_badge_clicked)
        self.wms_grid_delegate.tagBadgeClicked.connect(self._on_wms_badge_clicked)

        # set right click menu
        self.listProviders.setContextMenuPolicy(custom_context_menu)
        self.listProviders.customContextMenuRequested.connect(
            self.show_xyz_provider_context_menu
        )

        self.listBasemaps.setContextMenuPolicy(custom_context_menu)
        self.listBasemaps.customContextMenuRequested.connect(
            self.show_xyz_basemap_context_menu
        )

        self.listWmsProviders.setContextMenuPolicy(custom_context_menu)
        self.listWmsProviders.customContextMenuRequested.connect(
            self.show_wms_provider_context_menu
        )

        # Connect signals and slots
        self.btnLoadProvider.clicked.connect(self.import_providers)
        self.btnSaveProvider.clicked.connect(self.export_providers)

        # XYZ connections
        self.btnAddProvider.clicked.connect(self.add_xyz_provider)
        self.btnEditProvider.clicked.connect(self.edit_xyz_provider)
        self.btnRemoveProvider.clicked.connect(self.remove_xyz_provider)
        self.btnAddBasemap.clicked.connect(self.add_xyz_basemap)
        self.btnEditBasemap.clicked.connect(self.edit_xyz_basemap)
        self.btnRemoveBasemap.clicked.connect(self.remove_xyz_basemap)
        self.btnLoadBasemap.clicked.connect(self.load_xyz_basemap)
        self.listProviders.itemSelectionChanged.connect(self.on_provider_changed)
        self.listBasemaps.itemSelectionChanged.connect(
            self.on_basemap_selection_changed
        )
        self.listBasemapsGrid.itemSelectionChanged.connect(
            self.on_basemap_grid_selection_changed
        )

        # WMS connections
        self.btnAddWmsProvider.clicked.connect(self.add_wms_provider)
        self.btnEditWmsProvider.clicked.connect(self.edit_wms_provider)
        self.btnRemoveWmsProvider.clicked.connect(self.remove_wms_provider)
        self.btnRefreshWmsLayers.clicked.connect(self.refresh_wms_layers)
        self.btnEditWmsLayer.setVisible(False)
        self.btnLoadWmsLayer.clicked.connect(self.load_wms_layer)
        self.listWmsProviders.itemSelectionChanged.connect(self.on_wms_provider_changed)
        self.treeWmsLayers.itemSelectionChanged.connect(
            self.on_wms_layer_selection_changed
        )
        self.listWmsLayersGrid.itemSelectionChanged.connect(
            self.on_wms_layer_grid_selection_changed
        )

        # WMS layer context menus
        self.treeWmsLayers.setContextMenuPolicy(custom_context_menu)
        self.treeWmsLayers.customContextMenuRequested.connect(
            self.show_wms_layer_context_menu
        )
        self.listWmsLayersGrid.setContextMenuPolicy(custom_context_menu)
        self.listWmsLayersGrid.customContextMenuRequested.connect(
            self.show_wms_layer_grid_context_menu
        )

        # Sync Text/Gallery view between XYZ and WMS/WMTS tabs
        self.tabBasemapsView.currentChanged.connect(self._on_xyz_view_changed)
        self.tabWmsView.currentChanged.connect(self._on_wms_view_changed)

        # Tag filter state
        self._active_tag: str = "All"

        # Search filter state
        self._search_text_xyz: str = ""
        self._search_text_wms: str = ""

        # Setup tag filter combo — store English tag values as item data
        for tag in AVAILABLE_TAGS:
            display = self.tr(tag)
            self.tagFilterCombo.addItem(display, tag)
        self.tagFilterCombo.setCurrentIndex(0)
        self.tagFilterCombo.currentTextChanged.connect(self._on_tag_changed)

        # Connect search boxes
        self.searchBasemaps.textChanged.connect(self._on_xyz_search_changed)
        self.searchWmsLayers.textChanged.connect(self._on_wms_search_changed)

        # Load configurations
        self.load_default_basemaps()
        self.load_user_basemaps()

        # Apply persisted tag overrides for default-provider items
        self._tag_overrides = config_loader.load_tag_overrides(self.resources_dir)
        config_loader.apply_tag_overrides(self.providers_data, self._tag_overrides)

        # Select first selectable provider by default in both tabs
        for i in range(self.listProviders.count()):
            if self.listProviders.item(i).flags() & item_selectable:
                self.listProviders.setCurrentRow(i)
                break
        for i in range(self.listWmsProviders.count()):
            if self.listWmsProviders.item(i).flags() & item_selectable:
                self.listWmsProviders.setCurrentRow(i)
                break

        # Select Gallery view by default for both XYZ and WMS/WMTS tabs
        self.tabBasemapsView.setCurrentIndex(1)
        self.tabWmsView.setCurrentIndex(1)

        # Set layout stretch so list/tab widgets expand to fill space
        self.verticalLayout_2.setStretch(1, 1)  # listProviders
        self.verticalLayout_3.setStretch(1, 1)  # tabBasemapsView
        self.verticalLayout_4.setStretch(1, 1)  # listWmsProviders
        self.verticalLayout_5.setStretch(1, 1)  # tabWmsView

        # Detail Panel setup
        self._panel_width = 300
        self._details_visible = False
        self._setup_detail_panel()
        self._setup_details_toggle_button()

        # Refresh detail panel on selection changes
        self.listBasemaps.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.listBasemapsGrid.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.treeWmsLayers.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.listWmsLayersGrid.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.listProviders.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.listWmsProviders.itemSelectionChanged.connect(self._refresh_detail_panel)
        self.tabWidget.currentChanged.connect(self._on_detail_tab_changed)

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate("BasemapsDialog", message)

    def _on_tag_changed(self, tag_name: str) -> None:
        """Handle tag filter combo change."""
        self._active_tag = self.tagFilterCombo.currentData(user_role) or tag_name
        self._apply_tag_filter()

    def _tag_matches(self, item_data: dict | None, tag: str) -> bool:
        """Check if item data has a matching tag.

        Parameters
        ----------
        item_data : dict | None
            Basemap or layer data dictionary.
        tag : str
            Tag to check against.

        Returns
        -------
        bool
            True if item matches the tag filter.
        """
        if tag == "All":
            return True
        if not item_data or not isinstance(item_data, dict):
            return False
        item_tags = item_data.get("tags", [])
        return self._tag_list_matches(item_tags, tag)

    def _tag_list_matches(self, item_tags: list[str] | None, active_tag: str) -> bool:
        """Check whether a tag list matches the active filter.

        Parameters
        ----------
        item_tags : list[str] | None
            Tags assigned to a basemap or layer.
        active_tag : str
            Active filter value from the tag combo box.

        Returns
        -------
        bool
            True when the item should remain visible for the active tag.

        Notes
        -----
        Selecting ``Overlay`` matches both the root overlay tag and all
        ``Overlay/...`` subcategories. Other tags require an exact match.
        """
        if active_tag == "All":
            return True

        normalized_tags = [tag for tag in (item_tags or []) if isinstance(tag, str)]
        if not normalized_tags:
            return False

        if active_tag == OVERLAY_TAG:
            return any(
                tag == OVERLAY_TAG or tag.startswith(OVERLAY_TAG_PREFIX)
                for tag in normalized_tags
            )

        return active_tag in normalized_tags

    @staticmethod
    def _sort_key_by_tag(item: dict) -> int:
        """Return sort key for a basemap/layer based on its tags.

        Items are ordered by the first matching tag in TAG_SORT_ORDER.
        Items without a recognized tag are placed at the end.
        """
        if not isinstance(item, dict):
            return len(TAG_SORT_ORDER)
        item_tags = item.get("tags", [])
        if not item_tags:
            return len(TAG_SORT_ORDER)
        for tag in item_tags:
            if tag in TAG_SORT_ORDER:
                return TAG_SORT_ORDER.index(tag)
        return len(TAG_SORT_ORDER)

    def _tree_item_hidden_by_tag(self, tree_item: QTreeWidgetItem, tag: str) -> bool:
        """Check if a tree item should be hidden based on tag filtering.

        Recursively checks children. Returns True if item and all
        descendants should be hidden.

        Parameters
        ----------
        tree_item : QTreeWidgetItem
            The tree item to check.
        tag : str
            Active tag filter.

        Returns
        -------
        bool
            True if the item should be hidden.
        """
        if tag == "All":
            for i in range(tree_item.childCount()):
                tree_item.child(i).setHidden(False)
            return False

        item_data = tree_item.data(0, user_role)
        if item_data and isinstance(item_data, dict):
            item_tags = item_data.get("tags", [])
            if self._tag_list_matches(item_tags, tag):
                for i in range(tree_item.childCount()):
                    tree_item.child(i).setHidden(False)
                return False

        all_hidden = True
        for i in range(tree_item.childCount()):
            child = tree_item.child(i)
            child_hidden = self._tree_item_hidden_by_tag(child, tag)
            child.setHidden(child_hidden)
            if not child_hidden:
                all_hidden = False

        return all_hidden

    def _search_matches(self, text: str, search_text: str) -> bool:
        """Check if item text matches the search filter.

        Parameters
        ----------
        text : str
            Item display text.
        search_text : str
            Lowercase search text (empty = no filter).

        Returns
        -------
        bool
            True if item passes the search filter.
        """
        if not search_text:
            return True
        return search_text in text.lower()

    def _on_xyz_search_changed(self, text: str) -> None:
        """Handle XYZ search box text changes."""
        self._search_text_xyz = text
        self._apply_tag_filter()

    def _on_wms_search_changed(self, text: str) -> None:
        """Handle WMS search box text changes."""
        self._search_text_wms = text
        self._apply_tag_filter()

    def _provider_has_matching_items(self, provider: dict, active_tag: str) -> bool:
        """Check if a provider has any basemaps/layers matching the active tag.

        Parameters
        ----------
        provider : dict
            Provider data dictionary.
        active_tag : str
            The active tag filter.

        Returns
        -------
        bool
            True if at least one item matches or tag is "All".
        """
        if active_tag == "All":
            return True
        items = provider.get("basemaps", []) or provider.get("layers", [])
        return any(
            self._tag_list_matches(item.get("tags", []), active_tag) for item in items
        )

    def _apply_tag_filter(self) -> None:
        """Filter displayed basemaps/layers/providers based on active tag and search."""
        active_tag = self._active_tag
        search_xyz = (self._search_text_xyz or "").lower()
        search_wms = (self._search_text_wms or "").lower()

        # Filter XYZ providers
        for i in range(self.listProviders.count()):
            item = self.listProviders.item(i)
            item_data = item.data(user_role)
            if item_data and "data" in item_data:
                provider = item_data["data"]
                has_match = self._provider_has_matching_items(provider, active_tag)
                item.setHidden(not has_match)

        # Filter XYZ basemaps (list + grid)
        for widget in [self.listBasemaps, self.listBasemapsGrid]:
            for i in range(widget.count()):
                item = widget.item(i)
                data = item.data(user_role)
                item.setHidden(
                    not (
                        self._tag_matches(data, active_tag)
                        and self._search_matches(item.text(), search_xyz)
                    )
                )

        # Filter WMS providers
        for i in range(self.listWmsProviders.count()):
            item = self.listWmsProviders.item(i)
            item_data = item.data(user_role)
            if item_data and "data" in item_data:
                provider = item_data["data"]
                has_match = self._provider_has_matching_items(provider, active_tag)
                item.setHidden(not has_match)

        # Filter WMS layers (tree + grid)
        for i in range(self.treeWmsLayers.topLevelItemCount()):
            top_item = self.treeWmsLayers.topLevelItem(i)
            hidden = self._tree_item_hidden_by_tag(top_item, active_tag)
            if not hidden and search_wms:
                item_text = top_item.text(0).lower() if top_item.text(0) else ""
                if search_wms not in item_text:
                    hidden = True
            top_item.setHidden(hidden)

        for i in range(self.listWmsLayersGrid.count()):
            item = self.listWmsLayersGrid.item(i)
            data = item.data(user_role)
            item.setHidden(
                not (
                    self._tag_matches(data, active_tag)
                    and self._search_matches(item.text(), search_wms)
                )
            )

    def _get_user_separator_index(self) -> int:
        """Get the index of User separator in providers_data.

        Returns
        -------
        int
            Index of User separator, or -1 if not found
        """
        for i, p in enumerate(self.providers_data):
            if p.get("type") == "separator" and "User" in p.get("name", ""):
                return i
        return -1

    def _is_default_provider(self, provider: dict[str, Any]) -> bool:
        """Check if provider is a default (built-in) provider.

        Parameters
        ----------
        provider : dict[str, Any]
            Provider dictionary

        Returns
        -------
        bool
            True if provider is from default directory

        Notes
        -----
        Uses source_file path. Falls back to separator index for backward compatibility.
        """
        # Method 1: Check source_file (preferred)
        if "source_file" in provider:
            return "/providers/default/" in str(provider["source_file"])

        # Method 2: Fallback to old separator-based logic
        Logger.warning(
            f"Provider '{provider.get('name')}' missing source_file, using fallback"
        )
        try:
            provider_index = self.providers_data.index(provider)
            user_separator_index = self._get_user_separator_index()
            return user_separator_index >= 0 and provider_index < user_separator_index
        except ValueError:
            return False

    def reject(self):
        """Called when dialog is closed or cancelled."""
        if hasattr(self, "preview_manager"):
            self.preview_manager.cleanup()
        super().reject()

    def closeEvent(self, event):
        """Handle window close button."""
        if hasattr(self, "preview_manager"):
            self.preview_manager.cleanup()
        super().closeEvent(event)

    def _duplicate_provider_as_user(
        self, provider: dict[str, Any], suffix: str | None = None
    ) -> dict[str, Any]:
        """Create user copy of a provider.

        Parameters
        ----------
        provider : dict[str, Any]
            Source provider to duplicate
        suffix : str | None
            Suffix to add to name (default: " (Custom)"). If None, uses the
            translated fallback.

        Returns
        -------
        dict[str, Any]
            New provider dictionary with user ownership

        Notes
        -----
        - Copies all data except source_file
        - Adds creation timestamp
        - Modifies name to avoid conflicts
        - Does NOT save to disk (caller must call save_user_config)
        """
        if suffix is None:
            suffix = " " + QCoreApplication.translate("BasemapsDialog", "(Custom)")
        import copy
        import time

        new_provider = copy.deepcopy(provider)

        # Generate unique name
        base_name = f"{provider['name']}{suffix}"
        new_name = base_name
        counter = 1
        while any(p.get("name") == new_name for p in self.providers_data):
            new_name = f"{base_name} ({counter})"
            counter += 1

        new_provider["name"] = new_name
        new_provider["created_at"] = time.time()

        # Remove source_file - will be set when saved
        if "source_file" in new_provider:
            del new_provider["source_file"]

        Logger.info(
            f"Duplicated provider '{provider['name']}' as '{new_provider['name']}'"
        )
        return new_provider

    def load_default_basemaps(self):
        """Load default basemap configurations."""
        try:
            providers = config_loader.load_all_provider_files(
                self.resources_dir, "default"
            )

            if providers:
                Logger.info(
                    f"Loaded {len(providers)} default providers from individual files"
                )

                self.providers_data = [default_separator] + providers
                self.update_providers_list()
                return

            Logger.warning("No default configuration files found")

        except Exception as e:
            Logger.critical(f"Failed to load default configuration: {e}")
            MessageBox.critical(
                self.tr("Failed to load default configuration: {}").format(str(e)),
                self.tr("Error"),
                self,
            )

    def load_user_basemaps(self):
        """Load user basemap configurations."""
        try:
            providers = config_loader.load_all_provider_files(
                self.resources_dir, "user"
            )
            if providers:
                providers_with_time = [p for p in providers if "created_at" in p]
                providers_without_time = [p for p in providers if "created_at" not in p]
                # Sort providers by creation time (oldest first)
                # Providers without created_at will be placed at the beginning
                providers_with_time.sort(key=lambda x: x["created_at"])
                sorted_providers = providers_without_time + providers_with_time

                # Add separator to distinguish default from user providers
                self.providers_data.append(user_separator)
                self.providers_data.extend(sorted_providers)
                Logger.info(f"Loaded {len(providers)} user providers")
                self.update_providers_list()
        except Exception as e:
            Logger.critical(f"Failed to load user configuration: {e}")
            MessageBox.critical(
                self.tr("Failed to load user configuration: {}").format(str(e)),
                self.tr("Error"),
                self,
            )

    def import_providers(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import Configuration File"),
            "",
            self.tr(
                "ZIP files (*.zip);;YAML files (*.yaml *.yml);;JSON files (*.json)"
            ),
        )
        if not file_path:
            return

        if file_path.lower().endswith(".zip"):
            try:
                import shutil
                import tempfile
                import zipfile

                # Create temp directory
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Unzip file
                    with zipfile.ZipFile(file_path, "r") as zip_ref:
                        zip_ref.extractall(temp_dir)

                    # Find config file (YAML)
                    yaml_files = list(Path(temp_dir).glob("*.yaml")) + list(
                        Path(temp_dir).glob("*.yml")
                    )

                    config_file = None
                    if yaml_files:
                        config_file = yaml_files[0]
                    else:
                        raise Exception(
                            QCoreApplication.translate(
                                "BasemapsDialog",
                                "No YAML configuration file found in ZIP",
                            )
                        )

                    # Load config data using unified loader
                    data = config_loader.load_config_file(config_file)

                    # Copy icon files
                    icons_dir = Path(temp_dir) / "icons"
                    if icons_dir.exists():
                        target_icons_dir = self.icons_dir
                        for icon_file in icons_dir.glob("*"):
                            shutil.copy2(icon_file, target_icons_dir)

                    # Update data
                    self.providers_data.extend(data.get("providers", []))
                    self.update_providers_list()
                    self.save_user_config()

            except Exception as e:
                MessageBox.critical(
                    self.tr("Failed to import ZIP file: {}").format(str(e)),
                    self.tr("Error"),
                    self,
                )
        else:
            self.load_basemaps_from_file(file_path)
            self.save_user_config()

    def export_providers(self):
        """Export providers to ZIP file"""
        # Check if any items are selected
        selected_xyz_items = self.listProviders.selectedItems()
        selected_wms_items = self.listWmsProviders.selectedItems()

        if not selected_xyz_items and not selected_wms_items:
            # Ask user if they want to export all user-defined providers
            reply = MessageBox.question(
                self.tr(
                    "No providers selected. Do you want to export all user-defined providers?"
                ),
                self.tr("Export Configuration"),
                self,
            )
            if reply == MessageBox.NO:
                return

        # Set default filename
        default_filename = QCoreApplication.translate("BasemapsDialog", "providers.zip")
        if len(selected_xyz_items) + len(selected_wms_items) == 1:
            # If only one provider is selected, use its name as filename
            if selected_xyz_items:
                provider_name = selected_xyz_items[0].text()
            else:
                provider_name = selected_wms_items[0].text()
            default_filename = f"{provider_name}.zip"

        # Get save path
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Configuration File"),
            default_filename,
            self.tr("ZIP files (*.zip)"),
        )
        if not file_path:
            return

        try:
            # Collect providers and icons to export
            providers_to_export = []
            icon_files = set()

            if not selected_xyz_items and not selected_wms_items:
                # Find User separator index
                user_separator_index = self._get_user_separator_index()

                # Export all user-defined providers (after User separator)
                providers = (
                    self.providers_data[user_separator_index + 1 :]
                    if user_separator_index >= 0
                    else []
                )
                for provider in providers:
                    if provider.get("type") != "separator":
                        providers_to_export.append(provider)
                        if "icon" in provider:
                            icon_files.add(provider["icon"])
            else:
                # Process selected XYZ providers
                for item in selected_xyz_items:
                    provider_data = item.data(user_role)
                    if provider_data and provider_data.get("data"):
                        provider = provider_data["data"]
                        if provider.get("type") != "separator":
                            providers_to_export.append(provider)
                            if "icon" in provider:
                                icon_files.add(provider["icon"])

                # Process selected WMS providers
                for item in selected_wms_items:
                    provider_data = item.data(user_role)
                    if provider_data and provider_data.get("data"):
                        provider = provider_data["data"]
                        if provider.get("type") != "separator":
                            providers_to_export.append(provider)
                            if "icon" in provider:
                                icon_files.add(provider["icon"])

            # Create temp directory and save files
            import tempfile
            import zipfile

            with tempfile.TemporaryDirectory() as temp_dir:
                # Save as YAML file (new default format)
                yaml_path = Path(temp_dir) / "providers.yaml"
                config_loader.save_config_as_yaml(yaml_path, providers_to_export)

                # Create ZIP file
                with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    # Add YAML file
                    zipf.write(yaml_path, "providers.yaml")

                    # Add icon files
                    for icon_path in icon_files:
                        if icon_path.startswith("icons/"):
                            full_path = Path(__file__).parent / "resources" / icon_path
                            if full_path.exists():
                                zipf.write(full_path, icon_path)

            MessageBox.information(
                self.tr("Configuration saved successfully."),
                self.tr("Success"),
                self,
            )

        except Exception as e:
            MessageBox.critical(
                self.tr("Failed to save configuration: {}").format(str(e)),
                self.tr("Error"),
                self,
            )

    def load_basemaps_from_file(self, file_path):
        """Load basemaps from configuration file (YAML).

        Parameters
        ----------
        file_path : str
            Path to configuration file
        """
        try:
            # Load using unified loader (YAML)
            data = config_loader.load_config_file(file_path)

            # Mark as user-defined provider
            for provider in data.get("providers", []):
                provider["builtin"] = False

            # Merge with existing data
            existing_providers = {p["name"]: p for p in self.providers_data}
            for provider in data.get("providers", []):
                if provider["name"] in existing_providers:
                    # If builtin provider, create a new user-defined version
                    if existing_providers[provider["name"]].get("builtin", False):
                        provider["name"] = "{} {}".format(
                            provider["name"],
                            QCoreApplication.translate("BasemapsDialog", "(Custom)"),
                        )
                        self.providers_data.append(provider)
                    else:
                        # Update existing user provider's basemaps
                        if "basemaps" in provider:
                            existing_providers[provider["name"]]["basemaps"].extend(
                                provider["basemaps"]
                            )
                else:
                    # Add new provider
                    self.providers_data.append(provider)

            self.update_providers_list()

        except Exception as e:
            Logger.critical(f"Failed to load configuration file: {e}")
            MessageBox.critical(
                self.tr("Failed to load configuration file: {}").format(str(e)),
                self.tr("Error"),
                self,
            )

    def save_user_config(self):
        """Save user configuration as separate YAML files (one per provider).

        Saves user providers (those after User separator) to individual files in resources directory.
        Each provider gets its own file: resources/providers/user/{type}_{provider_name}.yaml
        """
        try:
            # Find User separator index (not Default separator)
            user_separator_index = self._get_user_separator_index()

            # Get user providers (after User separator)
            user_providers = (
                self.providers_data[user_separator_index + 1 :]
                if user_separator_index >= 0
                else []
            )

            if not user_providers:
                Logger.info("No user providers to save")
                return

            # Save each provider to its own file
            saved_files = config_loader.save_providers_separately(
                self.resources_dir, user_providers, prefix="user"
            )

            Logger.info(f"Saved {len(saved_files)} user provider files")

        except Exception as e:
            Logger.critical(f"Failed to save user configuration: {e}")
            MessageBox.critical(
                self.tr("Failed to save configuration: {}").format(str(e)),
                self.tr("Error"),
                self,
            )

    def update_providers_list(self):
        """Update provider list"""
        self.listProviders.clear()
        self.listWmsProviders.clear()

        # Set list icon size
        self.listProviders.setIconSize(QSize(15, 15))
        self.listWmsProviders.setIconSize(QSize(15, 15))

        def create_scaled_icon(icon_path):
            if icon_path.exists():
                original_icon = QIcon(str(icon_path))
                pixmap = original_icon.pixmap(QSize(15, 15))
                return QIcon(pixmap)
            return IconBasemaps

        # Add providers to corresponding lists
        for i, provider in enumerate(self.providers_data):
            # If separator, add non-selectable separator item
            if provider.get("type") == "separator":
                for list_widget in [self.listProviders, self.listWmsProviders]:
                    item = QListWidgetItem(self.tr(provider["name"]))
                    item.setFlags(item.flags() & ~item_enabled & ~item_selectable)
                    list_widget.addItem(item)
                continue

            # Create icon
            if "icon" in provider:
                icon_file = self.icons_dir / provider["icon"]
                provider_icon = create_scaled_icon(icon_file)
            else:
                provider_icon = IconBasemaps

            # Add to different lists based on type
            if provider.get("type") == "wms":
                item = QListWidgetItem(provider["name"])
                item.setIcon(provider_icon)
                item.setData(user_role, {"index": i, "data": provider})
                self.listWmsProviders.addItem(item)
            else:  # xyz type
                # Ensure provider has basemaps field
                if "basemaps" not in provider:
                    provider["basemaps"] = []

                item = QListWidgetItem(provider["name"])
                item.setIcon(provider_icon)
                item.setData(user_role, {"index": i, "data": provider})
                self.listProviders.addItem(item)

        # Ensure button states reflect initial (no selection) state
        self.on_basemap_selection_changed()

    def add_provider(self):
        dialog = ProviderInputDialog(self)
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                MessageBox.warning(
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                    self.tr("Warning"),
                    self,
                )
                return

            self.providers_data.append(provider_data)
            self.update_providers_list()
            self.save_user_config()

    def remove_provider(self):
        selected_items = self.listProviders.selectedItems()
        if not selected_items:
            return

        # Check if default providers are selected
        default_selected = any(
            self._is_default_provider(item.data(user_role)["data"])
            for item in selected_items
            if item.data(user_role) and item.data(user_role).get("data")
        )
        if default_selected:
            MessageBox.warning(
                self.tr("Default providers cannot be removed."),
                self.tr("Warning"),
                self,
            )
            return

        # Get provider names to delete
        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = MessageBox.question(
            self.tr('Are you sure you want to remove providers: "{}"?').format(
                names_str
            ),
            self.tr("Confirm Deletion"),
            self,
        )

        if reply == MessageBox.YES:
            # Collect indices to remove and providers to delete
            indices_to_remove = []
            providers_to_delete = []
            for item in selected_items:
                provider_data = item.data(user_role)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
                    providers_to_delete.append(provider_data["data"])

            # Sort indices from large to small, so deleting will not affect other indices
            indices_to_remove.sort(reverse=True)

            # Delete provider files and preview images
            for provider in providers_to_delete:
                config_loader.delete_provider_file(
                    self.resources_dir, provider, prefix="user"
                )
                # Delete preview images for this provider
                basemaps = provider.get("basemaps", [])
                self.preview_manager.delete_provider_previews(
                    provider["name"], basemaps, "xyz", is_default=False
                )

            # Delete provider from data
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

    def add_basemap(self):
        current_item = self.listProviders.currentItem()
        if not current_item:
            MessageBox.warning(
                self.tr("Please select a provider first."),
                self.tr("Warning"),
                self,
            )
            return

        dialog = BasemapInputDialog(self)
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = current_item.data(user_role)
            # Directly modify providers_data data
            self.providers_data[provider_data["index"]]["basemaps"].append(
                dialog.get_data()
            )
            self.update_providers_list()  # Refresh provider list
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def edit_basemap(self):
        current_provider = self.listProviders.currentItem()
        current_basemap = self.listBasemaps.currentItem()
        if not current_provider or not current_basemap:
            return

        provider_data = current_provider.data(user_role)
        basemap = current_basemap.data(user_role)

        dialog = BasemapInputDialog(self, basemap)
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            # Directly modify providers_data data
            provider = self.providers_data[provider_data["index"]]
            basemap_index = provider["basemaps"].index(basemap)
            provider["basemaps"][basemap_index] = dialog.get_data()
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def remove_basemap(self):
        current_provider = self.listProviders.currentItem()
        selected_basemaps = self.listBasemaps.selectedItems()
        if not current_provider or not selected_basemaps:
            return

        # Get basemap names to delete
        basemap_names = [item.text() for item in selected_basemaps]
        names_str = '", "'.join(basemap_names)

        reply = MessageBox.question(
            self.tr('Are you sure you want to remove basemaps: "{}"?').format(
                names_str
            ),
            self.tr("Confirm Deletion"),
            self,
        )

        if reply == MessageBox.YES:
            provider_data = current_provider.data(user_role)
            # Directly modify providers_data data
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(user_role) for item in selected_basemaps]

            # Delete preview images for removed basemaps
            for basemap in basemaps_to_remove:
                if basemap and basemap.get("name"):
                    self.preview_manager.delete_preview(
                        provider["name"], basemap["name"], "xyz", is_default=False
                    )

            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def load_selected_basemap(self):
        selected_items = self.listBasemaps.selectedItems()
        if not selected_items:
            return

        current_provider = self.listProviders.currentItem()
        if not current_provider:
            return

        for item in selected_items:
            basemap = item.data(user_role)
            self.load_xyz_basemap(basemap)

    def load_xyz_basemap(self):
        selected_items = self.listBasemaps.selectedItems()
        if not selected_items:
            return

        current_provider = self.listProviders.currentItem()
        token = str()
        token_param = DEFAULT_TOKEN_PARAM
        if current_provider:
            provider_data = current_provider.data(user_role)
            if provider_data:
                provider = provider_data.get("data", {})
                token = provider.get("token", "")
                token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
                # Warn if authentication is enabled but token is not set
                if "token" in provider and not token:
                    result = MessageBox.question(
                        self.tr(
                            "Provider '{}' requires an API token. "
                            "Set it now?"
                        ).format(provider.get("name", "")),
                        self.tr("Authentication Required"),
                        self,
                    )
                    if result == MessageBox.YES:
                        self.edit_provider_by_name(
                            provider.get("name", ""), "xyz"
                        )
                    return

        for item in selected_items:
            basemap = item.data(user_role)
            if not basemap:
                continue

            try:
                name = basemap["name"]
                tile_type = basemap.get("tile_type", "raster")

                if tile_type == "vector":
                    source_url = self._append_token(
                        basemap.get("url", ""), token, token_param
                    )
                    style_url = self._append_token(
                        basemap.get("style_url", ""), token, token_param
                    )
                    uri = QgsDataSourceUri()
                    uri.setParam("type", "xyz")
                    if source_url:
                        uri.setParam("url", source_url)
                    encoded_uri = str(uri.encodedUri(), "utf-8")
                    task = VectorTileLoadTask(encoded_uri, name, style_url)
                    # Hold reference to prevent garbage collection
                    self._vector_tile_tasks.append(task)
                    task.taskCompleted.connect(
                        lambda t=task: (
                            self._vector_tile_tasks.remove(t)
                            if t in self._vector_tile_tasks
                            else None
                        )
                    )
                    task.taskTerminated.connect(
                        lambda t=task: (
                            self._vector_tile_tasks.remove(t)
                            if t in self._vector_tile_tasks
                            else None
                        )
                    )
                    QgsApplication.taskManager().addTask(task)
                else:
                    url = self._append_token(basemap["url"], token, token_param)
                    uri = QgsDataSourceUri()
                    uri.setParam("type", "xyz")
                    uri.setParam("url", url)
                    layer = QgsRasterLayer(str(uri.encodedUri(), "utf-8"), name, "wms")
                    if layer.isValid():
                        QgsProject.instance().addMapLayer(layer)
                    else:
                        MessageBox.critical(
                            self.tr("Failed to load basemap: {}").format(name),
                            self.tr("Error"),
                            self,
                        )
            except (KeyError, TypeError) as e:
                MessageBox.critical(
                    self.tr("Invalid basemap data: {}").format(str(e)),
                    self.tr("Error"),
                    self,
                )

    def on_provider_changed(self):
        """update basemap list and disable edit/remove buttons for default providers"""
        from qgis.PyQt.QtCore import QTimer

        current_item = self.listProviders.currentItem()
        if not current_item:
            self.listBasemaps.clear()
            self.listBasemapsGrid.clear()
            self.btnEditBasemap.setEnabled(False)
            self.btnRemoveBasemap.setEnabled(False)
            self.btnEditProvider.setEnabled(False)
            self.btnRemoveProvider.setEnabled(False)
            return

        provider_data = current_item.data(user_role)
        if not provider_data or "data" not in provider_data:
            return

        # Clear immediately so old content disappears; populate deferred
        self.listBasemaps.clear()
        self.listBasemapsGrid.clear()
        self.btnEditBasemap.setEnabled(False)
        self.btnRemoveBasemap.setEnabled(False)

        # Capture everything needed for deferred population
        provider = provider_data["data"]
        provider_name = provider["name"]
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
        is_default_provider = self._is_default_provider(provider)
        provider_icon = IconBasemaps
        if "icon" in provider:
            icon_file = self.icons_dir / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))

        basemaps = [
            bm
            for bm in sorted(
                provider_data["data"].get("basemaps", []), key=self._sort_key_by_tag
            )
            if isinstance(bm, dict)
            and "name" in bm
            and ("url" in bm or bm.get("tile_type") == "vector")
        ]

        CHUNK = 15
        self._xyz_version = getattr(self, "_xyz_version", 0) + 1
        version = self._xyz_version

        def process_chunk(start: int):
            if self._xyz_version != version:
                return
            end = min(start + CHUNK, len(basemaps))
            for i in range(start, end):
                basemap = basemaps[i]
                tile_type = basemap.get("tile_type", "raster")
                protocol = "vector" if tile_type == "vector" else "xyz"

                item = QListWidgetItem(basemap["name"])
                item.setIcon(provider_icon)
                item.setData(user_role, basemap)
                self.listBasemaps.addItem(item)

                grid_item = QListWidgetItem(basemap["name"])
                grid_item.setData(user_role, basemap)
                grid_item.setData(user_role + 10, provider_icon)
                grid_item.setData(user_role + 12, protocol)
                grid_item.setToolTip(basemap["name"])
                bm_tags = basemap.get("tags", [])
                grid_item.setData(user_role + 11, bm_tags[0] if bm_tags else None)
                self.listBasemapsGrid.addItem(grid_item)

                # Issue preview inline (same as WMS)
                if tile_type == "vector":
                    preview_url = self._append_token(
                        basemap.get("url", ""), token, token_param
                    )
                    preview_style_url = self._append_token(
                        basemap.get("style_url", ""), token, token_param
                    )
                    if preview_url:
                        self.preview_manager.request_vector_preview(
                            provider_name,
                            basemap["name"],
                            preview_url,
                            preview_style_url,
                            is_default_provider,
                        )
                else:
                    preview_url = self._append_token(basemap["url"], token, token_param)
                    self.preview_manager.request_preview(
                        provider_name,
                        basemap["name"],
                        preview_url,
                        "xyz",
                        None,
                        is_default_provider,
                    )

            if end < len(basemaps):
                QCoreApplication.processEvents()
                QTimer.singleShot(5, lambda s=end: process_chunk(s))
            else:
                self.btnAddBasemap.setEnabled(not is_default_provider)
                self.btnEditProvider.setEnabled(not is_default_provider)
                self.btnRemoveProvider.setEnabled(not is_default_provider)
                self._apply_tag_filter()

        QTimer.singleShot(0, lambda: process_chunk(0))

    def on_wms_provider_changed(self):
        """Update layer tree when WMS provider changed (chunked to keep UI responsive)."""
        from qgis.PyQt.QtCore import QTimer

        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            self.treeWmsLayers.clear()
            self.listWmsLayersGrid.clear()
            self.btnEditWmsProvider.setEnabled(False)
            self.btnRemoveWmsProvider.setEnabled(False)
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        # Clear immediately so old content disappears; populate deferred
        self.treeWmsLayers.clear()
        self.listWmsLayersGrid.clear()
        self.btnEditWmsProvider.setEnabled(False)
        self.btnRemoveWmsProvider.setEnabled(False)

        provider = provider_data["data"]
        provider_name = provider["name"]
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
        is_default = self._is_default_provider(provider)
        provider_icon = IconBasemaps
        if "icon" in provider:
            icon_file = self.icons_dir / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
        preview_url_base = self._append_token(provider["url"], token, token_param)
        provider_service_type = provider.get("service_type", "wms")

        layers = sorted(
            provider_data["data"].get("layers", []), key=self._sort_key_by_tag
        )

        CHUNK = 15
        self._wms_version = getattr(self, "_wms_version", 0) + 1
        version = self._wms_version

        def process_chunk(start: int):
            if self._wms_version != version:
                return
            end = min(start + CHUNK, len(layers))
            for i in range(start, end):
                layer = layers[i]
                display_name = layer.get(
                    "layer_title",
                    layer.get("layer_name", self.tr("Unknown Layer")),
                )
                service_type = layer.get("service_type", provider_service_type)

                layer_item = QTreeWidgetItem([display_name])
                layer_item.setIcon(0, provider_icon)
                self.treeWmsLayers.addTopLevelItem(layer_item)

                crs_list = layer.get("crs", [])
                format_list = layer.get("format", [])
                if len(crs_list) <= 1 and len(format_list) <= 1:
                    layer_item.setData(0, user_role, layer)
                else:
                    layer_tags = layer.get("tags", [])
                    default_config = {
                        "layer_name": layer.get("layer_name"),
                        "layer_title": layer.get("layer_title"),
                        "crs": [crs_list[0]] if crs_list else [],
                        "format": format_list if format_list else [],
                        "styles": layer.get("styles", [""]),
                        "service_type": provider_service_type,
                        "tags": layer_tags,
                    }
                    layer_item.setData(0, user_role, default_config)

                grid_item = QListWidgetItem(display_name)
                grid_item.setData(user_role, layer)
                grid_item.setData(user_role + 10, provider_icon)
                grid_item.setData(user_role + 12, service_type)
                grid_item.setToolTip(display_name)
                layer_tags = layer.get("tags", [])
                grid_item.setData(user_role + 11, layer_tags[0] if layer_tags else None)
                self.listWmsLayersGrid.addItem(grid_item)

                self.preview_manager.request_preview(
                    provider_name,
                    display_name,
                    preview_url_base,
                    service_type,
                    layer,
                    is_default,
                )

            if end < len(layers):
                QCoreApplication.processEvents()
                QTimer.singleShot(5, lambda s=end: process_chunk(s))
            else:
                self.btnEditWmsProvider.setEnabled(not is_default)
                self.btnRemoveWmsProvider.setEnabled(not is_default)
                self._apply_tag_filter()

        QTimer.singleShot(0, lambda: process_chunk(0))

    def on_wms_layer_selection_changed(self):
        """Handle WMS layer selection changes to update button states."""
        self._sync_list_selections(self.treeWmsLayers, self.listWmsLayersGrid)
        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            return

        provider_data = current_provider.data(user_role)
        if not provider_data:
            return

        # Load button is always enabled (no need to disable for default providers)

    def update_basemaps_list(self):
        """update basemap list"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        # Set basemap list icon size
        self.listBasemaps.setIconSize(QSize(15, 15))

        # Get provider icon
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = self.icons_dir / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = IconBasemaps
        else:
            provider_icon = IconBasemaps

        # Update basemap list
        self.listBasemaps.clear()
        for basemap in provider_data["data"]["basemaps"]:
            item = QListWidgetItem(basemap["name"])
            item.setIcon(provider_icon)
            item.setData(user_role, basemap)
            self.listBasemaps.addItem(item)

    def on_basemap_selection_changed(self):
        """Handle basemap selection changes to update button states."""
        self._sync_list_selections(self.listBasemaps, self.listBasemapsGrid)
        current_provider = self.listProviders.currentItem()
        if not current_provider:
            self.btnEditBasemap.setEnabled(False)
            self.btnRemoveBasemap.setEnabled(False)
            return

        provider_data = current_provider.data(user_role)
        if not provider_data:
            self.btnEditBasemap.setEnabled(False)
            self.btnRemoveBasemap.setEnabled(False)
            return

        provider = provider_data.get("data")
        is_default = self._is_default_provider(provider)

        # Check if any basemaps are selected
        selected_basemaps = self.listBasemaps.selectedItems()
        has_selection = len(selected_basemaps) > 0

        # For default providers, keep edit/remove disabled regardless
        # For user providers, enable only if basemap is selected
        if is_default:
            self.btnEditBasemap.setEnabled(False)
            self.btnRemoveBasemap.setEnabled(False)
        else:
            self.btnEditBasemap.setEnabled(has_selection)
            self.btnRemoveBasemap.setEnabled(has_selection)

        # Load button is always enabled

    def on_basemap_changed(self):
        # no longer need to show details
        pass

    def add_xyz_provider(self):
        dialog = ProviderInputDialog(self, provider_type="xyz")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                MessageBox.warning(
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                    self.tr("Warning"),
                    self,
                )
                return

            # Initialize XYZ provider data
            provider_data.update({
                "type": "xyz",
                "basemaps": [],
                "created_at": __import__("time").time(),
            })

            # Add to data list
            self.providers_data.append(provider_data)

            # Update interface display
            self.update_providers_list()

            # Select new added provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if item and item.data(user_role):
                    if item.data(user_role)["data"]["name"] == provider_data["name"]:
                        self.listProviders.setCurrentItem(item)
                        break

            # Save config
            self.save_user_config()

    def remove_xyz_provider(self):
        """remove XYZ provider"""
        selected_items = self.listProviders.selectedItems()
        if not selected_items:
            MessageBox.warning(
                self.tr("Please select providers to remove."),
                self.tr("Warning"),
                self,
            )
            return

        # Check if default providers are selected
        default_selected = any(
            self._is_default_provider(item.data(user_role)["data"])
            for item in selected_items
            if item.data(user_role) and item.data(user_role).get("data")
        )
        if default_selected:
            MessageBox.warning(
                self.tr("Default providers cannot be removed."),
                self.tr("Warning"),
                self,
            )
            return

        # Get provider names to delete
        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = MessageBox.question(
            self.tr('Are you sure you want to remove providers: "{}"?').format(
                names_str
            ),
            self.tr("Confirm Deletion"),
            self,
        )

        if reply == MessageBox.YES:
            # Collect indices to remove and providers to delete
            indices_to_remove = []
            providers_to_delete = []
            for item in selected_items:
                provider_data = item.data(user_role)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
                    providers_to_delete.append(provider_data["data"])

            # Sort indices from large to small, so deleting will not affect other indices
            indices_to_remove.sort(reverse=True)

            # Delete provider files and preview images
            for provider in providers_to_delete:
                config_loader.delete_provider_file(
                    self.resources_dir, provider, prefix="user"
                )
                basemaps = provider.get("basemaps", [])
                Logger.info(
                    f"Deleting provider '{provider.get('name')}' "
                    f"with {len(basemaps)} basemaps"
                )
                self.preview_manager.delete_provider_previews(
                    provider["name"],
                    basemaps,
                    "xyz",
                    is_default=False,
                    url=provider.get("url", ""),
                )

            # Delete provider from data
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

            # Select the first non-separator provider so the UI does not
            # linger on the now-deleted provider and trigger stale preview
            # requests for it.
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role).get("data", {}).get("type") != "separator"
                ):
                    self.listProviders.setCurrentItem(item)
                    break

    def add_xyz_basemap(self):
        current_item = self.listProviders.currentItem()
        if not current_item:
            MessageBox.warning(
                self.tr("Please select a provider first."),
                self.tr("Warning"),
                self,
            )
            return

        dialog = BasemapInputDialog(self, provider_type="xyz")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = current_item.data(user_role)
            # Directly modify providers_data data
            self.providers_data[provider_data["index"]]["basemaps"].append(
                dialog.get_data()
            )
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def _save_tag_overrides(self, provider: dict[str, Any]) -> None:
        """Persist tag edits to the tag overrides file (works for all providers)."""
        provider_type = provider.get("type")
        provider_name = provider.get("name")
        if not provider_type or not provider_name:
            return

        entry: dict[str, dict[str, list[str]]] = {}
        if provider_type == "xyz":
            for bm in provider.get("basemaps", []):
                tags = bm.get("tags", [])
                if tags:
                    entry[bm["name"]] = {"tags": list(tags)}
        elif provider_type == "wms":
            for layer in provider.get("layers", []):
                tags = layer.get("tags", [])
                if tags:
                    entry[layer.get("layer_name", "")] = {"tags": list(tags)}

        # Prune removed tags: if an item no longer has tags, remove its entry
        self._tag_overrides.setdefault(provider_type, {})
        existing = self._tag_overrides[provider_type].get(provider_name, {})
        merged = {}
        for item_name, override in entry.items():
            merged[item_name] = override
        # Keep entries for items not in the current provider (they may be from
        # other providers with the same name – play it safe and only overwrite
        # what we just edited)
        for item_name, override in existing.items():
            if item_name not in merged:
                # Check if this item still exists in the provider and has tags
                still_exists = False
                if provider_type == "xyz":
                    still_exists = any(
                        bm.get("name") == item_name and bm.get("tags")
                        for bm in provider.get("basemaps", [])
                    )
                elif provider_type == "wms":
                    still_exists = any(
                        layer.get("layer_name") == item_name and layer.get("tags")
                        for layer in provider.get("layers", [])
                    )
                if still_exists:
                    merged[item_name] = override

        if merged:
            self._tag_overrides[provider_type][provider_name] = merged
        else:
            self._tag_overrides[provider_type].pop(provider_name, None)

        config_loader.save_tag_overrides(self.resources_dir, self._tag_overrides)

    def _save_to_provider_file(self, provider: dict[str, Any]) -> None:
        """Persist tag edits directly to the provider's config file.

        Also clears any stale overrides for this provider so that
        ``apply_tag_overrides`` does not overwrite the direct save on next load.
        """
        source_file = provider.get("source_file")
        if source_file:
            config_loader.save_provider_to_path(Path(source_file), provider)
        else:
            prefix = "default" if self._is_default_provider(provider) else "user"
            config_loader.save_provider_to_yaml(
                self.resources_dir, provider, prefix=prefix
            )

        # Remove any existing overrides for this provider — the config file is
        # now the source of truth, and we must not let stale overrides win.
        provider_type = provider.get("type")
        provider_name = provider.get("name")
        if provider_type and provider_name:
            type_overrides = self._tag_overrides.get(provider_type)
            if type_overrides and provider_name in type_overrides:
                del type_overrides[provider_name]
                config_loader.save_tag_overrides(
                    self.resources_dir,
                    self._tag_overrides,
                )

    def _edit_xyz_basemap_tags(self, basemap_data: dict) -> None:
        """Open tag-only editor for an XYZ basemap (from grid badge click)."""
        current_provider = self.listProviders.currentItem()
        if not current_provider:
            return
        provider_data = current_provider.data(user_role)
        if not provider_data:
            return
        basemap_name = basemap_data.get("name", "Unknown")

        dialog = TagEditDialog(self, basemap_name, basemap_data)
        exec_result = _run_qt_dialog(dialog)
        if exec_result != dialog_accepted:
            return
        new_tags = dialog.get_tags()
        save_mode = dialog.get_save_mode()
        QSettings("Basemaps", "Basemaps").setValue("tag_save_preference", save_mode)

        # Update in providers_data (use name match, not identity — same as WMS)
        provider_index = provider_data["index"]
        found = None
        for bm in self.providers_data[provider_index].get("basemaps", []):
            if isinstance(bm, dict) and bm.get("name") == basemap_name:
                found = bm
                break
        if found is not None:
            found["tags"] = new_tags

        # Update list and grid items in-place
        # Use dict() copy so Qt detects the change and emits dataChanged
        # Match by name (not identity) so repeated edits work correctly
        updated_data = dict(found) if found is not None else dict(basemap_data)
        for widget in [self.listBasemaps, self.listBasemapsGrid]:
            for i in range(widget.count()):
                item = widget.item(i)
                item_data = item.data(user_role)
                if (
                    isinstance(item_data, dict)
                    and item_data.get("name") == basemap_name
                ):
                    item.setData(user_role, updated_data)
                    item.setData(user_role + 11, new_tags[0] if new_tags else None)
                    break

        self._apply_tag_filter()
        self.listBasemapsGrid.viewport().update()
        # Use self.providers_data reference directly — the 'provider' variable
        # from QListWidgetItem.data() may be a stale QVariant copy
        canonical = self.providers_data[provider_index]
        if save_mode == "overrides":
            self._save_tag_overrides(canonical)
        else:
            self._save_to_provider_file(canonical)

    def edit_xyz_basemap(self):
        """edit XYZ basemap"""
        current_provider = self.listProviders.currentItem()
        current_basemap = self.listBasemaps.currentItem()

        # Fallback: try grid selection if list has no current item
        if not current_basemap:
            grid_selected = self.listBasemapsGrid.selectedItems()
            if grid_selected:
                for i in range(self.listBasemaps.count()):
                    item = self.listBasemaps.item(i)
                    if item and item.text() == grid_selected[0].text():
                        self.listBasemaps.setCurrentItem(item)
                        current_basemap = item
                        break

        if not current_provider or not current_basemap:
            MessageBox.warning(
                self.tr("Please select a basemap to edit."),
                self.tr("Warning"),
                self,
            )
            return

        provider_data = current_provider.data(user_role)
        basemap = current_basemap.data(user_role)

        dialog = BasemapInputDialog(self, basemap)
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            # Get edited data
            new_data = dialog.get_data()

            Logger.info(f"Edit basemap: new_data = {new_data}")

            # Update data
            provider_index = provider_data["index"]
            basemap_index = self.providers_data[provider_index]["basemaps"].index(
                basemap
            )
            self.providers_data[provider_index]["basemaps"][basemap_index] = new_data

            Logger.info(
                f"Updated providers_data[{provider_index}]['basemaps'][{basemap_index}]"
                f" = {self.providers_data[provider_index]['basemaps'][basemap_index]}"
            )

            # Update interface display
            self.update_providers_list()

            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_index
                ):
                    self.listProviders.setCurrentItem(item)
                    break

            # Save config
            self.save_user_config()

    def remove_xyz_basemap(self):
        current_provider = self.listProviders.currentItem()
        selected_basemaps = self.listBasemaps.selectedItems()
        if not current_provider or not selected_basemaps:
            MessageBox.warning(
                self.tr("Please select basemaps to remove."),
                self.tr("Warning"),
                self,
            )
            return

        names = [item.text() for item in selected_basemaps]
        names_str = '", "'.join(names)

        reply = MessageBox.question(
            self.tr('Are you sure you want to remove basemaps: "{}"?').format(
                names_str
            ),
            self.tr("Confirm Deletion"),
            self,
        )

        if reply == MessageBox.YES:
            provider_data = current_provider.data(user_role)
            # Directly modify providers_data data
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(user_role) for item in selected_basemaps]

            # Delete preview images for removed basemaps
            for basemap in basemaps_to_remove:
                if basemap and basemap.get("name"):
                    self.preview_manager.delete_preview(
                        provider["name"],
                        basemap["name"],
                        "xyz",
                        is_default=False,
                        url=basemap.get("url", ""),
                    )

            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def add_wms_provider(self):
        dialog = ProviderInputDialog(self, provider_type="wms")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                MessageBox.warning(
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                    self.tr("Warning"),
                    self,
                )
                return

            # Initialize WMS provider data
            provider_data.update({
                "type": "wms",
                "layers": [],  # Initialize empty layer list
                "created_at": __import__("time").time(),  # Add creation timestamp
            })

            # Add to data list
            self.providers_data.append(provider_data)

            # Update interface display
            self.update_providers_list()

            # Select new added provider
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if item and item.data(user_role):
                    if item.data(user_role)["data"]["name"] == provider_data["name"]:
                        self.listWmsProviders.setCurrentItem(item)
                        break

            # Save config
            self.save_user_config()

            # Automatically trigger refresh
            self.refresh_wms_layers()

    def remove_wms_provider(self):
        """remove WMS provider"""
        selected_items = self.listWmsProviders.selectedItems()
        if not selected_items:
            MessageBox.warning(
                self.tr("Please select providers to remove."),
                self.tr("Warning"),
                self,
            )
            return

        # Check if default providers are selected
        default_selected = any(
            self._is_default_provider(item.data(user_role)["data"])
            for item in selected_items
            if item.data(user_role) and item.data(user_role).get("data")
        )
        if default_selected:
            MessageBox.warning(
                self.tr("Default providers cannot be removed."),
                self.tr("Warning"),
                self,
            )
            return

        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = MessageBox.question(
            self.tr('Are you sure you want to remove providers: "{}"?').format(
                names_str
            ),
            self.tr("Confirm Deletion"),
            self,
        )

        if reply == MessageBox.YES:
            # Collect indices to remove and providers to delete
            indices_to_remove = []
            providers_to_delete = []
            for item in selected_items:
                provider_data = item.data(user_role)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
                    providers_to_delete.append(provider_data["data"])

            # Sort indices from large to small, so deleting will not affect other indices
            indices_to_remove.sort(reverse=True)

            # Delete provider files and preview images
            for provider in providers_to_delete:
                config_loader.delete_provider_file(
                    self.resources_dir, provider, prefix="user"
                )
                # Delete preview images for this WMS/WMTS provider
                layers = provider.get("layers", [])
                service_type = provider.get("service_type", "wms")
                self.preview_manager.delete_provider_previews(
                    provider["name"],
                    layers,
                    service_type,
                    is_default=False,
                    url=provider.get("url", ""),
                )

            # Delete provider from data
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

            # Select the first non-separator WMS provider so the UI does
            # not linger on the now-deleted provider.
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role).get("data", {}).get("type") != "separator"
                ):
                    self.listWmsProviders.setCurrentItem(item)
                    break

    def add_wms_layer(self):
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            MessageBox.warning(
                self.tr("Please select a WMS provider first."),
                self.tr("Warning"),
                self,
            )
            return

        dialog = BasemapInputDialog(self, provider_type="wms")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            provider_data = current_item.data(user_role)
            # Directly modify providers_data data
            self.providers_data[provider_data["index"]]["layers"].append(
                dialog.get_data()
            )
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listWmsProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def _get_default_layer_config(self, item: QTreeWidgetItem) -> dict | None:
        """
        Get default layer configuration from tree item.

        If the item is a parent node without complete data, recursively
        find the first leaf node and return its configuration.

        Parameters
        ----------
        item : QTreeWidgetItem
            The tree item to get configuration from.

        Returns
        -------
        dict | None
            Complete layer configuration, or None if not found.
        """
        # Try to get data from current item
        layer_data = item.data(0, user_role)

        # If current item has complete data, return it
        if layer_data and "layer_name" in layer_data:
            return layer_data

        # If it's a parent node, recursively find first leaf node
        if item.childCount() > 0:
            # Get first child
            first_child = item.child(0)
            return self._get_default_layer_config(first_child)

        # No valid data found
        return None

    def load_wms_layer(self):
        """
        Load selected WMS/WMTS layer(s) from tree to QGIS.

        Notes
        -----
        Handles tree items at any level:
        - Leaf nodes: Load with their specific configuration
        - Parent nodes: Load using the first child's configuration (default parameters)

        This allows users to quickly load layers without expanding the entire tree.
        """
        selected_items = self.treeWmsLayers.selectedItems()
        if not selected_items:
            return

        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            return

        provider_data = current_provider.data(user_role)
        provider = provider_data["data"]
        url = provider["url"].strip()
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)

        # Warn if authentication is enabled but token is not set
        if "token" in provider and not token:
            result = MessageBox.question(
                self.tr(
                    "Provider '{}' requires an API token. "
                    "Set it now?"
                ).format(provider.get("name", "")),
                self.tr("Authentication Required"),
                self,
            )
            if result == MessageBox.YES:
                self.edit_provider_by_name(
                    provider.get("name", ""), "wms"
                )
            return

        url = self._append_token(url, token, token_param)
        # Check if this is a WMTS service
        service_type = provider_data["data"].get("service_type", "wms")

        for item in selected_items:
            # Get layer configuration (from current item or first leaf)
            layer_data = self._get_default_layer_config(item)

            # Skip if no valid configuration found
            if not layer_data:
                continue

            # Determine service type from layer data or provider data
            layer_service_type = layer_data.get("service_type", service_type)

            if layer_service_type == "wmts":
                self._load_wmts_layer(url, layer_data)
            else:
                self._load_wms_layer(url, layer_data)

    def _load_wms_layer(self, url: str, layer_data: dict) -> None:
        """Load a WMS layer to QGIS.

        Parameters
        ----------
        url : str
            The WMS service URL.
        layer_data : dict
            Layer information dictionary.
        """
        params = {
            "url": url,
            "layers": layer_data["layer_name"],
            "format": layer_data["format"][0],
            "crs": layer_data["crs"][0],
            "styles": layer_data["styles"][0] if layer_data["styles"] else "",
        }

        uri = QgsDataSourceUri()
        for key, value in params.items():
            uri.setParam(key, value)

        layer = QgsRasterLayer(
            str(uri.encodedUri(), "utf-8"), layer_data["layer_title"], "wms"
        )

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        else:
            error_msg = layer.error().message() if layer.error() else "Unknown error"
            Logger.critical(
                f"Failed to load WMS layer: {layer_data['layer_title']} - {error_msg}"
            )
            MessageBox.critical(
                self.tr("Failed to load WMS layer: {}\n\nError: {}").format(
                    layer_data["layer_title"], error_msg
                ),
                self.tr("Error"),
                self,
            )

    def _load_wmts_layer(self, url: str, layer_data: dict) -> None:
        """Load a WMTS layer to QGIS.

        Parameters
        ----------
        url : str
            The WMTS service URL.
        layer_data : dict
            Layer information dictionary.
        """
        uri = QgsDataSourceUri()
        uri.setParam("url", url)
        uri.setParam("layers", layer_data["layer_name"])

        if layer_data.get("crs"):
            uri.setParam("tileMatrixSet", layer_data["crs"][0])

        if layer_data.get("format"):
            uri.setParam("format", layer_data["format"][0])

        if layer_data.get("styles"):
            uri.setParam("styles", layer_data["styles"][0])
        else:
            uri.setParam("styles", "")

        encoded_uri = str(uri.encodedUri(), "utf-8")
        Logger.info(f"Loading WMTS layer with URI: {encoded_uri}")

        layer = QgsRasterLayer(encoded_uri, layer_data["layer_title"], "wms")

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        else:
            error_msg = layer.error().message() if layer.error() else "Unknown error"
            Logger.critical(
                f"Failed to load WMTS layer: {layer_data['layer_title']} - {error_msg}"
            )
            MessageBox.critical(
                self.tr("Failed to load WMTS layer: {}\n\nError: {}").format(
                    layer_data["layer_title"], error_msg
                ),
                self.tr("Error"),
                self,
            )

    def refresh_wms_layers(self) -> None:
        """Refresh current selected WMS/WMTS provider's layer list.

        Fetches layers from the selected provider's URL using a background
        task to avoid blocking the QGIS UI.

        Notes
        -----
        This method creates a QgsTask to perform the HTTP request in the
        background. The UI is updated via signals when the task completes.
        """
        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            MessageBox.warning(
                self.tr("Please select a WMS provider first."),
                self.tr("Warning"),
                self,
            )
            return

        provider_data = current_provider.data(user_role)
        provider = provider_data["data"]
        url = provider["url"]
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
        fetch_url = self._append_token(url, token, token_param)
        provider_index = provider_data["index"]

        # Store context for callback
        self._pending_fetch_context = {
            "provider_data": provider_data,
            "provider_index": provider_index,
            "url": url,
        }

        # Create and configure task
        task = WMSFetchTask(fetch_url, timeout=30)
        task.signals.finished.connect(self._on_wms_fetch_complete)

        self._current_fetch_task = task

        # Add task to QGIS task manager
        QgsApplication.taskManager().addTask(task)

        Logger.info(
            self.tr("Fetching layers in background..."),
            notify_user=True,
        )

    def _on_wms_fetch_complete(self, result: FetchResult) -> None:
        """Handle fetch task completion.

        This method is called on the main thread when the background
        task completes (successfully or with error).

        Parameters
        ----------
        result : FetchResult
            The fetch operation result.
        """
        # Clear task reference
        self._current_fetch_task = None

        # Handle cancellation
        if not result.success and "cancelled" in result.error_message.lower():
            Logger.info("WMS fetch cancelled by user", notify_user=False)
            return

        # Handle error
        if not result.success:
            Logger.critical(f"Failed to fetch layers: {result.error_message}")
            MessageBox.critical(
                self.tr("Failed to fetch layers: {}").format(result.error_message),
                self.tr("Error"),
                self,
            )
            return

        # Get context
        context = self._pending_fetch_context
        if not context:
            Logger.warning(
                QCoreApplication.translate(
                    "BasemapsDialog", "Fetch completed but context was lost"
                )
            )
            return

        provider_index = context["provider_index"]
        url = context["url"]

        # Update provider data
        provider = self.providers_data[provider_index]
        detected_type = result.service_type.value

        # Check if default provider
        is_default_provider = self._is_default_provider(provider)

        if is_default_provider:
            # Create user copy with refreshed layers
            new_provider = self._duplicate_provider_as_user(provider)
            new_provider.update({
                "icon": provider.get("icon", "ui/icon.svg"),
                "type": "wms",
                "service_type": detected_type,
                "url": url,
                "layers": result.layers,
            })
            self.providers_data.append(new_provider)
            selected_provider_name = new_provider["name"]
        else:
            # Update existing user provider
            self.providers_data[provider_index].update({
                "icon": provider.get("icon", "ui/icon.svg"),
                "type": "wms",
                "service_type": detected_type,
                "url": url,
                "layers": result.layers,
            })
            selected_provider_name = provider["name"]

        # Update interface display
        self.update_providers_list()

        # Re-select provider
        for i in range(self.listWmsProviders.count()):
            item = self.listWmsProviders.item(i)
            if item and item.data(user_role):
                item_data = item.data(user_role)
                if item_data["data"]["name"] == selected_provider_name:
                    self.listWmsProviders.setCurrentItem(item)
                    break

        # Save config
        self.save_user_config()

        # Clear context
        self._pending_fetch_context = None

        # Show success message
        MessageBox.information(
            self.tr("Successfully refreshed {} layers.").format(detected_type.upper()),
            self.tr("Success"),
            self,
        )

    def show_xyz_provider_context_menu(self, position):
        current_item = self.listProviders.currentItem()
        if not current_item or not current_item.data(user_role):
            return

        provider_data = current_item.data(user_role)
        provider = provider_data.get("data")

        # Only show context menu for default providers (Duplicate)
        if not provider or not self._is_default_provider(provider):
            return

        menu = QMenu()
        duplicate_action = menu.addAction(self.tr("Duplicate as User Provider"))

        action = _run_qt_menu(menu, self.listProviders.mapToGlobal(position))

        if action == duplicate_action:
            self.duplicate_xyz_provider()

    def show_xyz_basemap_context_menu(self, position):
        current_item = self.listBasemaps.currentItem()
        if not current_item:
            return

        menu = QMenu()
        edit_action = menu.addAction(self.tr("Edit"))

        action = _run_qt_menu(menu, self.listBasemaps.mapToGlobal(position))

        if action == edit_action:
            self.edit_xyz_basemap()

    def show_wms_provider_context_menu(self, position):
        current_item = self.listWmsProviders.currentItem()
        if not current_item or not current_item.data(user_role):
            return

        provider_data = current_item.data(user_role)
        provider = provider_data.get("data")

        # Only show context menu for default providers (Duplicate)
        if not provider or not self._is_default_provider(provider):
            return

        menu = QMenu()
        duplicate_action = menu.addAction(self.tr("Duplicate as User Provider"))

        action = _run_qt_menu(menu, self.listWmsProviders.mapToGlobal(position))

        if action == duplicate_action:
            self.duplicate_wms_provider()

    def _get_selected_wms_layer(self) -> dict | None:
        """Get the currently selected WMS/WMTS layer data from tree or grid.

        Returns
        -------
        dict | None
            Layer data dictionary, or None if nothing selected.
        """
        # Try tree selection first
        selected = self.treeWmsLayers.selectedItems()
        if selected:
            item = selected[0]
            data = item.data(0, user_role)
            if data and "layer_name" in data:
                return data
            # Try to find leaf data
            if item.childCount() > 0:
                leaf = item.child(0)
                leaf_data = leaf.data(0, user_role)
                if leaf_data and "layer_name" in leaf_data:
                    return leaf_data
                if leaf.childCount() > 0:
                    leaf2 = leaf.child(0)
                    return leaf2.data(0, user_role)

        # Fall back to grid selection
        selected = self.listWmsLayersGrid.selectedItems()
        if selected:
            return selected[0].data(user_role)

        return None

    def _update_wms_tree_layer_tags(
        self, item: QTreeWidgetItem, layer_name: str, tags: list[str]
    ) -> bool:
        """Update stored tags for a WMS/WMTS tree item and descendants.

        Parameters
        ----------
        item : QTreeWidgetItem
            Tree item to inspect.
        layer_name : str
            Layer identifier to update.
        tags : list[str]
            New tag values.

        Returns
        -------
        bool
            True if the item or one of its descendants was updated.
        """
        updated = False
        item_data = item.data(0, user_role)
        if isinstance(item_data, dict) and item_data.get("layer_name") == layer_name:
            item_data = dict(item_data)
            item_data["tags"] = list(tags)
            item.setData(0, user_role, item_data)
            updated = True

        for child_index in range(item.childCount()):
            child = item.child(child_index)
            if self._update_wms_tree_layer_tags(child, layer_name, tags):
                updated = True

        return updated

    def _update_wms_layer_tags_in_views(self, layer_name: str, tags: list[str]) -> None:
        """Update cached WMS/WMTS layer tags in tree and gallery views.

        Parameters
        ----------
        layer_name : str
            Layer identifier to update.
        tags : list[str]
            New tag values.
        """
        for item_index in range(self.treeWmsLayers.topLevelItemCount()):
            item = self.treeWmsLayers.topLevelItem(item_index)
            self._update_wms_tree_layer_tags(item, layer_name, tags)

        for item_index in range(self.listWmsLayersGrid.count()):
            grid_item = self.listWmsLayersGrid.item(item_index)
            grid_data = grid_item.data(user_role)
            if (
                isinstance(grid_data, dict)
                and grid_data.get("layer_name") == layer_name
            ):
                grid_data = dict(grid_data)
                grid_data["tags"] = list(tags)
                grid_item.setData(user_role, grid_data)
                grid_item.setData(user_role + 11, tags[0] if tags else None)

    def edit_wms_layer_tags(self) -> None:
        """Edit tags for the selected WMS/WMTS layer."""
        layer = self._get_selected_wms_layer()
        if not layer:
            MessageBox.warning(
                self.tr("Please select a WMS/WMTS layer first."),
                self.tr("Warning"),
                self,
            )
            return

        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            return

        provider_data = current_provider.data(user_role)
        if not provider_data:
            return

        layer_name = layer.get("layer_name")
        if not layer_name:
            Logger.critical("EDIT_LAYER: Selected layer is missing layer_name")
            return

        layer_title = layer.get("layer_title", layer.get("layer_name", "Unknown"))
        dialog = TagEditDialog(self, layer_title, layer)
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            new_tags = dialog.get_tags()
            save_mode = dialog.get_save_mode()
            QSettings("Basemaps", "Basemaps").setValue("tag_save_preference", save_mode)

            # Find the exact layer in providers_data by name
            provider_index = provider_data["index"]
            layers = self.providers_data[provider_index].get("layers", [])
            found_lyr = None
            for lyr in layers:
                if lyr.get("layer_name") == layer_name:
                    found_lyr = lyr
                    break
            Logger.info(
                f"EDIT_LAYER: name={layer_name}, "
                f"dialog_tags={new_tags}, "
                f"old_layer_tags={found_lyr.get('tags') if found_lyr else 'NOT FOUND'}"
            )

            if found_lyr is None:
                Logger.critical("EDIT_LAYER: Could not find layer in providers_data!")
                return

            # Update tags in providers_data
            found_lyr["tags"] = new_tags
            current_provider.setData(user_role, provider_data)

            # Also update the layer dict from tree/grid
            layer["tags"] = new_tags

            Logger.info(
                f"EDIT_LAYER: after update, "
                f"providers_data[{provider_index}].layers "
                f"[name={found_lyr['layer_name']}] tags = {found_lyr['tags']}"
            )

            # Update all cached tree/gallery item payloads so the next edit dialog
            # opens with the just-saved tag without requiring a plugin reload.
            self._update_wms_layer_tags_in_views(layer_name, new_tags)

            # Re-apply the tag filter to reflect changes
            self._apply_tag_filter()
            self.listWmsLayersGrid.viewport().update()
            self.treeWmsLayers.viewport().update()

            # Persist tag edits
            # Use self.providers_data reference directly — 'provider' from
            # QListWidgetItem.data() may be a stale QVariant copy
            canonical = self.providers_data[provider_index]
            if save_mode == "overrides":
                self._save_tag_overrides(canonical)
            else:
                self._save_to_provider_file(canonical)

    def _on_xyz_badge_clicked(self, index: QModelIndex) -> None:
        """Handle click on tag badge in XYZ grid — open tag-only editor."""
        grid_item = self.listBasemapsGrid.itemFromIndex(index)
        if not grid_item:
            return
        basemap_data = grid_item.data(user_role)
        if not basemap_data:
            return
        self._edit_xyz_basemap_tags(basemap_data)

    def _on_wms_badge_clicked(self, index: QModelIndex) -> None:
        """Handle click on tag badge in WMS grid."""
        item = self.listWmsLayersGrid.itemFromIndex(index)
        if not item:
            return
        # editorEvent consumed the event, so selection wasn't updated
        self.listWmsLayersGrid.setCurrentItem(item)
        item.setSelected(True)
        self.edit_wms_layer_tags()

    def show_wms_layer_context_menu(self, position):
        """Show right-click context menu for WMS tree layers."""
        current_item = self.treeWmsLayers.currentItem()
        if not current_item:
            return

        # Get layer data
        data = current_item.data(0, user_role)
        if not data or "layer_name" not in data:
            # If parent node, try to find a child with data
            if current_item.childCount() > 0:
                child = current_item.child(0)
                data = child.data(0, user_role)
                if not data and child.childCount() > 0:
                    data = child.child(0).data(0, user_role)
            if not data:
                return

        menu = QMenu()
        edit_action = menu.addAction(self.tr("Edit"))

        action = _run_qt_menu(menu, self.treeWmsLayers.mapToGlobal(position))

        if action == edit_action:
            self.edit_wms_layer_tags()

    def show_wms_layer_grid_context_menu(self, position):
        """Show right-click context menu for WMS grid layers."""
        current_item = self.listWmsLayersGrid.currentItem()
        if not current_item:
            return

        data = current_item.data(user_role)
        if not data:
            return

        menu = QMenu()
        edit_action = menu.addAction(self.tr("Edit"))

        action = _run_qt_menu(menu, self.listWmsLayersGrid.mapToGlobal(position))

        if action == edit_action:
            self.edit_wms_layer_tags()

    def edit_provider_by_name(self, name: str, provider_type: str) -> None:
        """Open the edit dialog for a provider identified by name and type.

        For default providers, duplicates first then opens the edit dialog
        on the newly created user copy.

        Parameters
        ----------
        name : str
            The provider's display name.
        provider_type : str
            ``"xyz"`` or ``"wms"``.
        """
        if provider_type == "wms":
            provider_list = self.listWmsProviders
        else:
            provider_list = self.listProviders

        for i in range(provider_list.count()):
            item = provider_list.item(i)
            if item and item.data(user_role):
                if item.data(user_role)["data"]["name"] == name:
                    provider_list.setCurrentItem(item)
                    provider = item.data(user_role)["data"]

                    if self._is_default_provider(provider):
                        new_provider = self._duplicate_provider_as_user(provider)
                        self.providers_data.append(new_provider)
                        self.update_providers_list()
                        # Select the newly created user copy
                        for j in range(provider_list.count()):
                            new_item = provider_list.item(j)
                            if (
                                new_item
                                and new_item.data(user_role)
                                and new_item.data(user_role)["data"]["name"]
                                == new_provider["name"]
                            ):
                                provider_list.setCurrentItem(new_item)
                                provider = new_provider
                                break
                        self.save_user_config()

                    # Open the edit dialog directly
                    if provider_type == "wms":
                        dialog = ProviderInputDialog(
                            self, provider, provider_type="wms"
                        )
                        dialog.focus_token_field()
                        exec_result = _run_qt_dialog(dialog)
                        if exec_result == dialog_accepted:
                            new_data = dialog.get_data()
                            new_data["type"] = "wms"
                            new_data["layers"] = provider.get("layers", [])
                            new_data["url"] = dialog.url_edit.text()
                            if "source_file" in provider:
                                new_data["source_file"] = provider["source_file"]
                            provider_data = item.data(user_role)
                            self.providers_data[provider_data["index"]] = new_data
                            self.update_providers_list()
                            self.save_user_config()
                    else:
                        dialog = ProviderInputDialog(
                            self, provider, provider_type="xyz"
                        )
                        dialog.focus_token_field()
                        exec_result = _run_qt_dialog(dialog)
                        if exec_result == dialog_accepted:
                            new_data = dialog.get_data()
                            new_data["type"] = "xyz"
                            new_data["basemaps"] = provider.get("basemaps", [])
                            if "source_file" in provider:
                                new_data["source_file"] = provider["source_file"]
                            provider_data = item.data(user_role)
                            self.providers_data[provider_data["index"]] = new_data
                            self.update_providers_list()
                            self.save_user_config()
                    return

        Logger.warning(f"Provider '{name}' not found in {provider_type} list")

    def edit_xyz_provider(self):
        """Edit selected XYZ provider"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            MessageBox.warning(
                self.tr("Please select a provider to edit."),
                self.tr("Warning"),
                self,
            )
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        provider = provider_data["data"]

        # Check if it's a default provider
        if self._is_default_provider(provider):
            MessageBox.warning(
                self.tr("Default providers cannot be edited."),
                self.tr("Warning"),
                self,
            )
            return

        # Open edit dialog
        dialog = ProviderInputDialog(self, provider, provider_type="xyz")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            # Get edited data
            new_data = dialog.get_data()
            new_data["type"] = "xyz"
            new_data["basemaps"] = provider.get("basemaps", [])
            # Preserve source_file so renames can clean up the old file
            if "source_file" in provider:
                new_data["source_file"] = provider["source_file"]

            # Update data
            self.providers_data[provider_data["index"]] = new_data

            # Update interface display
            self.update_providers_list()

            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break

            # Save config
            self.save_user_config()

    def edit_wms_provider(self):
        """Edit selected WMS/WMTS provider"""
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            MessageBox.warning(
                self.tr("Please select a WMS provider to edit."),
                self.tr("Warning"),
                self,
            )
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        provider = provider_data["data"]

        # Check if it's a default provider
        if self._is_default_provider(provider):
            MessageBox.warning(
                self.tr("Default providers cannot be edited."),
                self.tr("Warning"),
                self,
            )
            return

        # Open edit dialog
        dialog = ProviderInputDialog(self, provider, provider_type="wms")
        exec_result = _run_qt_dialog(dialog)
        if exec_result == dialog_accepted:
            new_data = dialog.get_data()
            new_data["type"] = "wms"
            new_data["layers"] = provider.get("layers", [])
            new_data["url"] = dialog.url_edit.text()
            # Preserve source_file so renames can clean up the old file
            if "source_file" in provider:
                new_data["source_file"] = provider["source_file"]

            # Update data
            self.providers_data[provider_data["index"]] = new_data

            # Update interface display
            self.update_providers_list()

            # Re-select current provider
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if (
                    item
                    and item.data(user_role)
                    and item.data(user_role)["index"] == provider_data["index"]
                ):
                    self.listWmsProviders.setCurrentItem(item)
                    break

            # Save config
            self.save_user_config()

    def duplicate_xyz_provider(self):
        """Duplicate selected XYZ provider as user-defined version."""
        current_item = self.listProviders.currentItem()
        if not current_item:
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        provider = provider_data["data"]

        # Only allow duplicating default providers
        if not self._is_default_provider(provider):
            MessageBox.information(
                self.tr("Only default providers can be duplicated."),
                self.tr("Information"),
                self,
            )
            return

        # Create user copy
        new_provider = self._duplicate_provider_as_user(provider)

        # Add to providers list
        self.providers_data.append(new_provider)

        # Update UI
        self.update_providers_list()

        # Select the new provider
        for i in range(self.listProviders.count()):
            item = self.listProviders.item(i)
            if item and item.data(user_role):
                if item.data(user_role)["data"]["name"] == new_provider["name"]:
                    self.listProviders.setCurrentItem(item)
                    break

        # Save config
        self.save_user_config()

        MessageBox.information(
            self.tr("Provider duplicated as '{}'").format(new_provider["name"]),
            self.tr("Success"),
            self,
        )

    def duplicate_wms_provider(self):
        """Duplicate selected WMS provider as user-defined version."""
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        provider = provider_data["data"]

        # Only allow duplicating default providers
        if not self._is_default_provider(provider):
            MessageBox.information(
                self.tr("Only default providers can be duplicated."),
                self.tr("Information"),
                self,
            )
            return

        # Create user copy
        new_provider = self._duplicate_provider_as_user(provider)

        # Add to providers list
        self.providers_data.append(new_provider)

        # Update UI
        self.update_providers_list()

        # Select the new provider
        for i in range(self.listWmsProviders.count()):
            item = self.listWmsProviders.item(i)
            if item and item.data(user_role):
                if item.data(user_role)["data"]["name"] == new_provider["name"]:
                    self.listWmsProviders.setCurrentItem(item)
                    break

        # Save config
        self.save_user_config()

        MessageBox.information(
            self.tr("Provider duplicated as '{}'").format(new_provider["name"]),
            self.tr("Success"),
            self,
        )

    def _on_preview_ready(self, key, image_path):
        """Handle preview image ready event."""
        # Update grid views (both XYZ and WMS if they match the key)
        # key format is "{provider_name}_{layer_name}"
        for grid_view in [self.listBasemapsGrid, self.listWmsLayersGrid]:
            for i in range(grid_view.count()):
                item = grid_view.item(i)
                current_item_key = (
                    f"{self._get_current_provider_name(grid_view)}_{item.text()}"
                )
                if current_item_key == key:
                    pixmap = QPixmap(image_path)
                    item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
                    # Trigger repaint
                    grid_view.update()

        # Refresh detail panel preview if visible
        if self._details_visible:
            self._refresh_detail_panel()

    def _get_current_provider_name(self, grid_view):
        if grid_view == self.listBasemapsGrid:
            provider_list = self.listProviders
        else:
            provider_list = self.listWmsProviders

        current_provider = provider_list.currentItem()
        return current_provider.text() if current_provider else ""

    def _sync_list_selections(self, source, target):
        """Helper to synchronize selection between list/tree and grid."""
        target.blockSignals(True)
        target.clearSelection()

        if isinstance(source, QListWidget):
            selected_items = source.selectedItems()
            selected_texts = [item.text() for item in selected_items]
        else:  # QTreeWidget
            selected_items = source.selectedItems()
            selected_texts = [item.text(0) for item in selected_items]

        for i in range(target.count()):
            item = target.item(i)
            if item.text() in selected_texts:
                item.setSelected(True)
        target.blockSignals(False)

    def _sync_tree_selection_from_grid(self, grid, tree):
        """Helper to synchronize tree selection from grid."""
        tree.blockSignals(True)
        tree.clearSelection()
        selected_texts = [item.text() for item in grid.selectedItems()]

        # Traverse tree to find matching items
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0) in selected_texts:
                item.setSelected(True)
        tree.blockSignals(False)

    def on_basemap_grid_selection_changed(self):
        self._sync_list_selections(self.listBasemapsGrid, self.listBasemaps)
        # Sync current item from grid to list
        grid_current = self.listBasemapsGrid.currentItem()
        if grid_current:
            for i in range(self.listBasemaps.count()):
                item = self.listBasemaps.item(i)
                if item and item.text() == grid_current.text():
                    self.listBasemaps.setCurrentItem(item)
                    break
        self.on_basemap_selection_changed()

    def on_wms_layer_grid_selection_changed(self):
        self._sync_tree_selection_from_grid(self.listWmsLayersGrid, self.treeWmsLayers)
        self.on_wms_layer_selection_changed()

    @staticmethod
    def _append_token(
        url: str, token: str, token_param: str = DEFAULT_TOKEN_PARAM
    ) -> str:
        """Append token as query parameter to URL.

        Parameters
        ----------
        url : str
            The base URL.
        token : str
            The API token/key to append.
        token_param : str
            Query parameter name used by the provider.

        Returns
        -------
        str
            URL with token appended.
        """
        if not url or not token:
            return url
        param_name = token_param.strip() or DEFAULT_TOKEN_PARAM
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode({param_name: token})}"

    def _on_xyz_view_changed(self, index: int) -> None:
        """Sync WMS/WMTS view when XYZ view changes.

        Parameters
        ----------
        index : int
            The new tab index (0=Text, 1=Gallery).
        """
        # Block signals to prevent infinite loop
        self.tabWmsView.blockSignals(True)
        self.tabWmsView.setCurrentIndex(index)
        self.tabWmsView.blockSignals(False)

    def _on_wms_view_changed(self, index: int) -> None:
        """Sync XYZ view when WMS/WMTS view changes.

        Parameters
        ----------
        index : int
            The new tab index (0=Text, 1=Gallery).
        """
        # Block signals to prevent infinite loop
        self.tabBasemapsView.blockSignals(True)
        self.tabBasemapsView.setCurrentIndex(index)
        self.tabBasemapsView.blockSignals(False)

    # ── Detail Panel ──────────────────────────────────────────────

    def _setup_detail_panel(self) -> None:
        """Create the detail panel and restructure the dialog layout.

        Wraps the toolbar (``horizontalLayout``) and ``tabWidget`` in a
        vertical column, then places that column alongside a ``QFrame``
        detail panel in an outermost horizontal layout.  This ensures the
        toolbar buttons stay fixed when the panel is toggled.
        """
        # Hidden placeholder to park the detail panel when it is closed,
        # so it does not contribute to the splitter's minimum size hint.
        self._park_widget = QWidget()

        # ── Extract toolbar and tabWidget from the main layout ─────
        self.verticalLayout.removeWidget(self.tabWidget)
        toolbar_item = self.verticalLayout.takeAt(0)

        # ── Left column: toolbar + tabWidget ───────────────────────
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)
        if toolbar_item and toolbar_item.layout():
            left_col.addLayout(toolbar_item.layout())
        left_col.addWidget(self.tabWidget, 1)

        # ── Outermost horizontal splitter ───────────────────────────
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.setHandleWidth(2)

        left_widget = QWidget()
        left_widget.setLayout(left_col)
        self._content_splitter.addWidget(left_widget)

        # ── Detail panel frame ─────────────────────────────────────
        self.detailsPanel = QFrame()
        self.detailsPanel.setObjectName("detailsPanel")
        self.detailsPanel.setMinimumWidth(self._panel_width)
        self.detailsPanel.setFrameShape(QFrame.Shape.StyledPanel)
        self.detailsPanel.setStyleSheet("QFrame#detailsPanel {  background: #FAFBFC;}")

        panel_layout = QVBoxLayout(self.detailsPanel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(0)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        # Content widget inside scroll area
        self._panel_content = QWidget()
        self._panel_content.setStyleSheet("background: transparent;")
        self._panel_content_layout = QVBoxLayout(self._panel_content)
        self._panel_content_layout.setContentsMargins(0, 0, 10, 0)
        self._panel_content_layout.setSpacing(10)

        # Preview thumbnail label
        self._panel_preview = QLabel()
        self._panel_preview.setMinimumHeight(120)
        self._panel_preview.setMaximumHeight(180)
        self._panel_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._panel_preview.setStyleSheet(
            "QLabel {"
            "  background: #F0F2F5;"
            "  border: 1px solid #DCE4EC;"
            "  border-radius: 6px;"
            "}"
        )
        self._panel_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._panel_content_layout.addWidget(self._panel_preview)

        # Info text — single rich-text label for all metadata
        self._panel_info = QLabel()
        self._panel_info.setWordWrap(True)
        self._panel_info.setOpenExternalLinks(False)
        self._panel_info.setTextFormat(Qt.TextFormat.RichText)
        self._panel_info.setStyleSheet(
            "QLabel {  color: #2C3E50;  font-size: 11px;  background: transparent;}"
        )
        self._panel_info.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._panel_info.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._panel_info.linkActivated.connect(self._on_panel_link_clicked)
        self._panel_content_layout.addWidget(self._panel_info)

        self._panel_content_layout.addStretch()

        scroll.setWidget(self._panel_content)
        panel_layout.addWidget(scroll)

        # ── Add to splitter and main layout ────────────────────────
        self._content_splitter.addWidget(self.detailsPanel)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 0)
        self.detailsPanel.hide()
        self.verticalLayout.addWidget(self._content_splitter, 1)

    def _setup_details_toggle_button(self) -> None:
        """Configure the details toggle button (defined in the .ui file)."""
        self.btnToggleDetails.setIcon(
            QgsApplication.getThemeIcon("mActionAtlasLast.svg")
        )
        self.btnToggleDetails.setIconSize(QSize(18, 18))
        self.btnToggleDetails.toggled.connect(self._on_details_toggled)

    def _on_details_toggled(self, checked: bool) -> None:
        """Handle detail panel toggle — resize dialog to grow/shrink."""
        if checked:
            pre_panel_width = self.width()
            # Re-add panel to splitter (it was parked on _park_widget during hide)
            self.detailsPanel.setParent(self._content_splitter)
            self.detailsPanel.show()
            self.resize(pre_panel_width + self._panel_width, self.height())
        else:
            panel_width = self.detailsPanel.width()
            if panel_width <= 0:
                splitter_sizes = self._content_splitter.sizes()
                if len(splitter_sizes) > 1:
                    panel_width = splitter_sizes[1]
            if panel_width <= 0:
                panel_width = self._panel_width

            target_width = max(1, self.width() - panel_width)
            self.setMinimumWidth(min(self.minimumWidth(), target_width))
            self.detailsPanel.hide()
            # Park the panel under a hidden placeholder so it no longer
            # contributes to the splitter's minimum size hint.  Without
            # this the left-widget's expanded width prevents the dialog
            # from shrinking to the current content width.
            self.detailsPanel.setParent(self._park_widget)
            self.resize(target_width, self.height())

        self._details_visible = checked
        self._refresh_detail_panel()

    def _on_detail_tab_changed(self, _index: int) -> None:
        """Refresh the detail panel when switching between XYZ and WMS tabs."""
        self._refresh_detail_panel()

    def _refresh_detail_panel(self) -> None:
        """Update the detail panel for the currently selected item."""
        if not self._details_visible:
            return

        current_tab = self.tabWidget.currentIndex()

        if current_tab == 0:  # XYZ / Vector Tiles
            layer_data, protocol = self._get_current_xyz_layer()
            provider_data = self._get_current_provider(self.listProviders, "xyz")
            if layer_data:
                self._render_layer_detail(layer_data, provider_data, protocol)
            elif provider_data:
                self._render_provider_detail(provider_data)
            else:
                self._render_empty_detail()
        else:  # WMS / WMTS
            layer_data, protocol = self._get_current_wms_layer()
            provider_data = self._get_current_provider(self.listWmsProviders, "wms")
            if layer_data:
                self._render_layer_detail(layer_data, provider_data, protocol)
            elif provider_data:
                self._render_provider_detail(provider_data)
            else:
                self._render_empty_detail()

    # ── Data helpers ───────────────────────────────────────────

    @staticmethod
    def _get_current_provider(list_widget: QListWidget, _svc_type: str) -> dict | None:
        """Return the *provider* data dict for the selected provider item."""
        item = list_widget.currentItem()
        if not item:
            return None
        data = item.data(user_role)
        if not data or "data" not in data:
            return None
        return data["data"]

    def _get_current_xyz_layer(self) -> tuple[dict | None, str]:
        """Return (layer_dict, protocol) for the selected XYZ basemap."""
        # Prefer currently visible view
        if self.tabBasemapsView.currentIndex() == 1:  # Gallery
            items = self.listBasemapsGrid.selectedItems()
        else:
            items = self.listBasemaps.selectedItems()

        if items:
            basemap = items[0].data(user_role)
            if isinstance(basemap, dict):
                protocol = "vector" if basemap.get("tile_type") == "vector" else "xyz"
                return basemap, protocol
        return None, ""

    def _get_current_wms_layer(self) -> tuple[dict | None, str]:
        """Return (layer_dict, protocol) for the selected WMS/WMTS layer."""
        if self.tabWmsView.currentIndex() == 0:  # Tree (Text)
            items = self.treeWmsLayers.selectedItems()
        else:
            items = self.listWmsLayersGrid.selectedItems()

        if items:
            item = items[0]
            # QTreeWidgetItem needs (column, role); QListWidgetItem needs (role)
            if isinstance(item, QTreeWidgetItem):
                layer = item.data(0, user_role)
            else:
                layer = item.data(user_role)
            if isinstance(layer, dict):
                provider_data = self._get_current_provider(self.listWmsProviders, "wms")
                if provider_data:
                    protocol = provider_data.get("service_type", "wms")
                else:
                    protocol = layer.get("service_type", "wms")
                return layer, protocol
        return None, ""

    # ── Rendering ───────────────────────────────────────────────

    def _render_layer_detail(
        self,
        layer_data: dict,
        provider_data: dict | None,
        protocol: str,
    ) -> None:
        """Populate the detail panel with layer + provider metadata."""
        # ── Preview ──────────────────────────────────────────────
        name = layer_data.get("name") or layer_data.get("layer_title", "")
        key = f"{provider_data.get('name', '')}_{name}" if provider_data else f"_{name}"
        # Try to find a cached preview pixmap from the grid views
        pixmap = self._find_preview_pixmap(key)
        if pixmap:
            preview_w = max(100, self.detailsPanel.width() - 20)
            preview_h = int(preview_w * 0.6)
            self._panel_preview.setPixmap(
                pixmap.scaled(
                    preview_w,
                    preview_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self._panel_preview.clear()
            # Show provider icon placeholder
            if provider_data:
                icon_file = self.icons_dir / provider_data.get("icon", "")
                if icon_file.exists():
                    self._panel_preview.setPixmap(QIcon(str(icon_file)).pixmap(48, 48))

        # ── Info HTML ────────────────────────────────────────────
        parts: list[str] = []

        # Layer section
        parts.append(
            '<h3 style="margin:0 0 4px 0;color:#2C3E50;font-size:12px;">'
            + self.tr("Layer Information")
            + "</h3>"
        )
        parts.append(self._info_row(self.tr("Name"), self._esc(name)))

        # Tags — clickable colour badges, reuse gallery tag-editor flow
        tags = layer_data.get("tags", [])
        if tags:
            tag_spans = []
            for t in tags:
                tc = TAG_COLORS.get(t, "#999")
                tc_hex = tc.name() if hasattr(tc, "name") else tc
                display = t[t.find("/") + 1 :] if "/" in t else t
                tag_spans.append(
                    f'<a href="tag:{self._esc(t)}" '
                    f'style="background:{tc_hex};color:#fff;'
                    f"padding:1px 5px;border-radius:3px;font-size:10px;"
                    f"margin-right:3px;text-decoration:none;"
                    f'font-weight:bold;">{self._esc(display)}</a>'
                )
            parts.append(self._info_row(self.tr("Tag"), " ".join(tag_spans)))

        # Protocol — plain text
        proto_label = {
            "xyz": "XYZ Tile",
            "vector": "Vector Tile",
            "wms": "WMS",
            "wmts": "WMTS",
        }
        parts.append(
            self._info_row(
                self.tr("Protocol"),
                self._esc(proto_label.get(protocol, protocol.upper())),
            )
        )

        # URLs — truncated display, hover tooltip shows full URL, click copies
        url = layer_data.get("url") or layer_data.get("resource_url", "")
        if url:
            display_url = self._truncate_url(url)
            parts.append(
                self._info_row(
                    self.tr("URL"),
                    f'<a href="copy:{self._esc(url)}" '
                    f'title="{self._esc(url)}" '
                    f'style="color:#2C3E50;text-decoration:none;'
                    f'font-size:10px;font-family:monospace;">'
                    f"{self._esc(display_url)}</a>",
                )
            )
        style_url = layer_data.get("style_url", "")
        if style_url:
            display_style_url = self._truncate_url(style_url)
            parts.append(
                self._info_row(
                    self.tr("Style URL"),
                    f'<a href="copy:{self._esc(style_url)}" '
                    f'title="{self._esc(style_url)}" '
                    f'style="color:#2C3E50;text-decoration:none;'
                    f'font-size:10px;font-family:monospace;">'
                    f"{self._esc(display_style_url)}</a>",
                )
            )

        # CRS / Format (WMS/WMTS)
        crs = layer_data.get("crs", [])
        if crs:
            crs_str = ", ".join(crs) if isinstance(crs, list) else str(crs)
            parts.append(self._info_row(self.tr("CRS"), self._esc(crs_str)))
        fmt = layer_data.get("format", [])
        if fmt:
            fmt_str = ", ".join(fmt) if isinstance(fmt, list) else str(fmt)
            parts.append(self._info_row(self.tr("Format"), self._esc(fmt_str)))

        # Layer Metadata (from basemap config, inline in Layer Info)
        layer_website = layer_data.get("website", "")
        layer_copyright = layer_data.get("copyright", "")
        layer_terms = layer_data.get("terms_of_use", "")
        layer_desc = layer_data.get("description", "")

        if layer_website:
            display_lw = self._truncate_url(layer_website)
            parts.append(
                self._info_row(
                    "🌐︎ " + self.tr("Website"),
                    f'<a href="{self._esc(layer_website)}" '
                    f'title="{self._esc(layer_website)}" '
                    f'style="color:#3498DB;">{self._esc(display_lw)}</a>',
                )
            )
        if layer_copyright:
            parts.append(
                self._info_row(
                    "© " + self.tr("Copyright"),
                    self._esc(layer_copyright),
                )
            )
        if layer_terms:
            display_lt = self._truncate_url(layer_terms)
            parts.append(
                self._info_row(
                    "📄 " + self.tr("Terms of Use"),
                    f'<a href="{self._esc(layer_terms)}" '
                    f'title="{self._esc(layer_terms)}" '
                    f'style="color:#3498DB;">{self._esc(display_lt)}</a>',
                )
            )
        if layer_desc:
            parts.append(
                self._info_row(
                    self.tr("Description"),
                    self._esc(layer_desc).replace("\n", "<br>"),
                )
            )

        # Provider section
        if provider_data:
            parts.append(
                '<h3 style="margin:10px 0 4px 0;color:#2C3E50;font-size:12px;">'
                + self.tr("Provider Information")
                + "</h3>"
            )
            parts.append(
                self._info_row(
                    self.tr("Name"), self._esc(provider_data.get("name", ""))
                )
            )
            service_type = provider_data.get("service_type", "")
            if service_type:
                parts.append(
                    self._info_row(
                        self.tr("Protocol"),
                        self._esc(service_type.upper()),
                    )
                )

        copyright_text = provider_data.get("copyright", "")
        if copyright_text:
            parts.append(
                self._info_row(
                    "© " + self.tr("Copyright"),
                    self._esc(copyright_text),
                )
            )

        website = provider_data.get("website", "")
        if website:
            display_website = self._truncate_url(website)
            parts.append(
                self._info_row(
                    "🌐︎ " + self.tr("Website"),
                    f'<a href="{self._esc(website)}" '
                    f'title="{self._esc(website)}" '
                    f'style="color:#3498DB;">{self._esc(display_website)}</a>',
                )
            )

        terms = provider_data.get("terms_of_use", "")
        if terms:
            display_terms = self._truncate_url(terms)
            parts.append(
                self._info_row(
                    "📄 " + self.tr("Terms of Use"),
                    f'<a href="{self._esc(terms)}" '
                    f'title="{self._esc(terms)}" '
                    f'style="color:#3498DB;">{self._esc(display_terms)}</a>',
                )
            )

            desc = provider_data.get("description", "")
            if desc:
                parts.append(
                    self._info_row(
                        self.tr("Description"),
                        self._esc(desc).replace("\n", "<br>"),
                    )
                )

        self._panel_info.setText("".join(parts))

    def _render_provider_detail(self, provider_data: dict) -> None:
        """Render detail panel with provider metadata only (no layer selected)."""
        self._panel_preview.clear()
        icon_file = self.icons_dir / provider_data.get("icon", "")
        if icon_file.exists():
            self._panel_preview.setPixmap(QIcon(str(icon_file)).pixmap(48, 48))

        parts: list[str] = []
        parts.append(
            '<h3 style="margin:0 0 4px 0;color:#2C3E50;font-size:12px;">'
            + self.tr("Provider Information")
            + "</h3>"
        )
        parts.append(
            self._info_row(self.tr("Name"), self._esc(provider_data.get("name", "")))
        )
        service_type = provider_data.get("service_type", "")
        if service_type:
            parts.append(
                self._info_row(
                    self.tr("Protocol"),
                    self._esc(service_type.upper()),
                )
            )

        copyright_text = provider_data.get("copyright", "")
        if copyright_text:
            parts.append(
                self._info_row("© " + self.tr("Copyright"), self._esc(copyright_text))
            )
        website = provider_data.get("website", "")
        if website:
            display_website = self._truncate_url(website)
            parts.append(
                self._info_row(
                    "🌐︎ " + self.tr("Website"),
                    f'<a href="{self._esc(website)}" '
                    f'title="{self._esc(website)}" '
                    f'style="color:#3498DB;">{self._esc(display_website)}</a>',
                )
            )
        terms = provider_data.get("terms_of_use", "")
        if terms:
            display_terms = self._truncate_url(terms)
            parts.append(
                self._info_row(
                    "📄 " + self.tr("Terms of Use"),
                    f'<a href="{self._esc(terms)}" '
                    f'title="{self._esc(terms)}" '
                    f'style="color:#3498DB;">{self._esc(display_terms)}</a>',
                )
            )
        desc = provider_data.get("description", "")
        if desc:
            parts.append(
                self._info_row(
                    self.tr("Description"),
                    self._esc(desc).replace("\n", "<br>"),
                )
            )

        parts.append(
            '<p style="color:#7F8C8D;font-style:italic;margin-top:8px;">'
            + self.tr("Select a layer to see details")
            + "</p>"
        )
        self._panel_info.setText("".join(parts))

    def _render_empty_detail(self) -> None:
        """Show placeholder content when nothing is selected."""
        self._panel_preview.clear()
        self._panel_info.setText(
            '<p style="color:#7F8C8D;font-style:italic;">'
            + self.tr("Select a provider or layer to see details")
            + "</p>"
        )

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _esc(text: str) -> str:
        """Escape HTML entities in *text*."""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _truncate_url(url: str, max_len: int = 80) -> str:
        """Truncate a long URL for display."""
        if len(url) <= max_len:
            return url
        return url[: max_len - 3] + "..."

    @staticmethod
    def _info_row(label: str, value: str) -> str:
        """Return an HTML table row with a thin separator below."""
        return (
            f'<table style="margin:0;font-size:11px;" width="100%">'
            f"<tr>"
            f'<td style="color:#7F8C8D;white-space:nowrap;'
            f'padding-right:8px;vertical-align:top;width:1%;">'
            f"{label}</td>"
            f'<td style="color:#2C3E50;vertical-align:top;'
            f'text-align:right;word-wrap:break-word;overflow-wrap:break-word;">'
            f"{value}</td>"
            f"</tr></table>"
            f'<table style="margin:0;" width="100%">'
            f'<tr><td style="border-bottom:1px solid #E0E0E0;'
            f'font-size:0;line-height:0;">&nbsp;</td></tr></table>'
        )

    def _find_preview_pixmap(self, key: str) -> QPixmap | None:
        """Look up a cached preview pixmap from the grid views by *key*.

        *key* has the form ``"{provider_name}_{layer_name}"``.
        """
        for grid_view in [self.listBasemapsGrid, self.listWmsLayersGrid]:
            provider_name = self._get_current_provider_name(grid_view)
            for i in range(grid_view.count()):
                item = grid_view.item(i)
                item_key = f"{provider_name}_{item.text()}"
                if item_key == key:
                    pix = item.data(Qt.ItemDataRole.DecorationRole)
                    if isinstance(pix, QPixmap) and not pix.isNull():
                        return pix
        return None

    def _on_panel_link_clicked(self, link: str) -> None:
        """Handle clicks on links in the detail panel info text.

        Supported schemes:

        * ``copy:...`` — copy the payload to the system clipboard.
        * ``tag:...`` — open the tag editor (reuses the gallery badge flow).
        * anything else — open in the system browser.
        """
        if link.startswith("copy:"):
            url = link[5:]
            QApplication.clipboard().setText(url)
            from qgis.PyQt.QtGui import QCursor

            QToolTip.showText(
                QCursor.pos(),
                self.tr("✓ Copied!"),
                self,
            )
            Logger.info(f"Detail panel copied URL: {url}")
        elif link.startswith("tag:"):
            tag = link[4:]
            current_tab = self.tabWidget.currentIndex()
            if current_tab == 0:  # XYZ
                layer_data, _ = self._get_current_xyz_layer()
                if layer_data:
                    self._edit_xyz_basemap_tags(layer_data)
            else:  # WMS
                self.edit_wms_layer_tags()
        else:
            from qgis.PyQt.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(link))


def _translate_button_box(button_box: QDialogButtonBox) -> None:
    """Set translated text on standard buttons using plugin translations."""
    standard_map = {
        button_ok: "OK",
        button_cancel: "Cancel",
    }
    for btn_id, text_key in standard_map.items():
        btn = button_box.button(btn_id)
        if btn:
            btn.setText(QCoreApplication.translate("BasemapsDialog", text_key))


class ProviderInputDialog(QDialog):
    def __init__(self, parent=None, provider=None, provider_type="xyz"):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Add/Edit Provider"))
        self.plugin_dir = Path(__file__).parent
        self.icons_dir = self.plugin_dir / "resources" / "icons"
        self.provider_type = provider_type

        layout = QVBoxLayout(self)

        # Name input
        name_layout = QHBoxLayout()
        name_label = QLabel(self.tr("Name:"))
        self.name_edit = QLineEdit()
        if provider:
            self.name_edit.setText(provider.get("name", ""))
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Icon input
        icon_layout = QHBoxLayout()
        icon_label = QLabel(self.tr("Icon:"))
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("basemaps.svg")
        if provider:
            self.icon_edit.setText(provider.get("icon", ""))
        self.icon_button = QPushButton(self.tr("Browse..."))
        self.icon_button.clicked.connect(self.browse_icon)
        icon_layout.addWidget(icon_label)
        icon_layout.addWidget(self.icon_edit)
        icon_layout.addWidget(self.icon_button)
        layout.addLayout(icon_layout)

        # URL input (only show when WMS type)
        if provider_type == "wms":
            url_layout = QHBoxLayout()
            url_label = QLabel(self.tr("URL:"))
            self.url_edit = QLineEdit()
            if provider:
                self.url_edit.setText(provider.get("url", ""))
            url_layout.addWidget(url_label)
            url_layout.addWidget(self.url_edit)
            layout.addLayout(url_layout)

        self.token_auth_widget = TokenAuthWidget(self, provider)
        layout.addWidget(self.token_auth_widget)

        # ── Provider Metadata (optional) ─────────────────────────
        meta_group = QGroupBox(self.tr("Provider Metadata (optional)"))
        meta_layout = QVBoxLayout(meta_group)

        # Website
        website_layout = QHBoxLayout()
        website_label = QLabel(self.tr("Website:"))
        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://...")
        if provider:
            self.website_edit.setText(provider.get("website", ""))
        website_layout.addWidget(website_label)
        website_layout.addWidget(self.website_edit)
        meta_layout.addLayout(website_layout)

        # Copyright
        copyright_layout = QHBoxLayout()
        copyright_label = QLabel(self.tr("Copyright:"))
        self.copyright_edit = QLineEdit()
        self.copyright_edit.setPlaceholderText("© ...")
        if provider:
            self.copyright_edit.setText(provider.get("copyright", ""))
        copyright_layout.addWidget(copyright_label)
        copyright_layout.addWidget(self.copyright_edit)
        meta_layout.addLayout(copyright_layout)

        # Terms of Use
        terms_layout = QHBoxLayout()
        terms_label = QLabel(self.tr("Terms of Use:"))
        self.terms_edit = QLineEdit()
        self.terms_edit.setPlaceholderText("https://...")
        if provider:
            self.terms_edit.setText(provider.get("terms_of_use", ""))
        terms_layout.addWidget(terms_label)
        terms_layout.addWidget(self.terms_edit)
        meta_layout.addLayout(terms_layout)

        # Description
        desc_layout = QVBoxLayout()
        desc_label = QLabel(self.tr("Description:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText(
            self.tr("Brief description of the provider...")
        )
        if provider:
            self.desc_edit.setText(provider.get("description", ""))
        desc_layout.addWidget(desc_label)
        desc_layout.addWidget(self.desc_edit)
        meta_layout.addLayout(desc_layout)

        layout.addWidget(meta_group)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        _translate_button_box(button_box)
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def browse_icon(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select Icon File"),
            str(self.icons_dir),
            self.tr("Image Files (*.png *.jpg *.svg *.ico);;All Files (*)"),
        )
        if file_path:
            src = Path(file_path)
            dest = self.icons_dir / src.name
            if src.resolve() != dest.resolve():
                import shutil

                shutil.copy2(str(src), str(dest))
            self.icon_edit.setText(src.name)

    def _validate_and_accept(self):
        """Warn if authentication is enabled but token is not set, then accept."""
        warning = self.token_auth_widget.get_warning()
        if warning:
            MessageBox.warning(warning, self.tr("Authentication"), self)
        self.accept()

    def get_data(self):
        data = {
            "name": self.name_edit.text(),
            "icon": self.icon_edit.text().strip() or "basemaps.svg",
        }

        # Token
        data.update(self.token_auth_widget.get_data())

        if self.provider_type == "wms":
            data["url"] = self.url_edit.text()

        # Optional provider metadata — only include non-empty values
        website = self.website_edit.text().strip()
        if website:
            data["website"] = website
        copyright_text = self.copyright_edit.text().strip()
        if copyright_text:
            data["copyright"] = copyright_text
        terms = self.terms_edit.text().strip()
        if terms:
            data["terms_of_use"] = terms
        desc = self.desc_edit.text().strip()
        if desc:
            data["description"] = desc

        return data

    def focus_token_field(self):
        """Enable auth and focus the token input field."""
        self.token_auth_widget.setChecked(True)
        self.token_auth_widget.token_edit.setFocus()
        self.token_auth_widget.token_edit.selectAll()


class TokenAuthWidget(QGroupBox):
    """Provider token and token-parameter input widget (checkable)."""

    def __init__(self, parent: QDialog | None = None, provider: dict | None = None):
        super().__init__(parent)
        self.setTitle(self.tr("Authentication"))
        self.setCheckable(True)
        self.setChecked(False)

        provider_data = provider or {}
        # Auth was enabled if the provider has a "token" key at all
        # (may be empty string when user checked auth but hasn't set the key yet)
        has_token = "token" in provider_data

        layout = QVBoxLayout(self)

        # Well-known provider selector
        wk_layout = QHBoxLayout()
        wk_label = QLabel(self.tr("Provider:"))
        self.wk_combo = QComboBox()
        self.wk_combo.addItem("")
        for name in WELL_KNOWN_XYZ_PROVIDERS:
            self.wk_combo.addItem(name)
        self.wk_combo.currentTextChanged.connect(self._on_well_known_changed)
        wk_layout.addWidget(wk_label)
        wk_layout.addWidget(self.wk_combo, 1)
        layout.addLayout(wk_layout)

        token_layout = QHBoxLayout()
        token_label = QLabel(self.tr("Token:"))
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText(self.tr("API token / key"))
        self.token_edit.setText(provider_data.get("token", ""))
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.token_edit)
        layout.addLayout(token_layout)

        token_param_layout = QHBoxLayout()
        token_param_label = QLabel(self.tr("Token Parameter:"))
        self.token_param_combo = QComboBox()
        self.token_param_combo.setEditable(True)
        self.token_param_combo.addItems(TOKEN_PARAM_OPTIONS)
        self.token_param_combo.setCurrentText(
            provider_data.get("token_param", DEFAULT_TOKEN_PARAM)
        )
        token_param_layout.addWidget(token_param_label)
        token_param_layout.addWidget(self.token_param_combo)
        layout.addLayout(token_param_layout)

        # Enable/disable fields based on check state
        self.wk_combo.setEnabled(False)
        self.token_edit.setEnabled(False)
        self.token_param_combo.setEnabled(False)
        self.toggled.connect(self._on_toggled)

        # If editing a provider that already has a token, check it
        if has_token:
            self.setChecked(True)

    def _on_well_known_changed(self, name: str) -> None:
        """Set token parameter when a well-known provider is selected."""
        wk = WELL_KNOWN_XYZ_PROVIDERS.get(name)
        if not wk:
            return
        self.token_param_combo.setCurrentText(wk["token_param"])

    def _on_toggled(self, checked: bool) -> None:
        self.wk_combo.setEnabled(checked)
        self.token_edit.setEnabled(checked)
        self.token_param_combo.setEnabled(checked)

    def is_auth_enabled(self) -> bool:
        """Return True if the authentication checkbox is checked."""
        return self.isChecked()

    def get_data(self) -> dict[str, str]:
        """Return token-related provider fields.

        Returns an empty dict only when the checkbox is unchecked.
        When checked, always returns token/token_param (even if empty) so
        the authentication intent is preserved across edits.
        """
        if not self.isChecked():
            return {}
        token = self.token_edit.text().strip()
        token_param = self.token_param_combo.currentText().strip()
        return {
            "token": token,
            "token_param": token_param or DEFAULT_TOKEN_PARAM,
        }

    def get_warning(self) -> str:
        """Return a warning message if auth is enabled but token is missing."""
        if not self.isChecked():
            return ""
        token = self.token_edit.text().strip()
        if not token:
            return self.tr(
                "Authentication is enabled but no API token/key is set. "
                "Please click the Edit button on the provider to set it."
            )
        return ""


class BasemapInputDialog(QDialog):
    def __init__(self, parent=None, basemap=None, provider_type=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Add/Edit Basemap"))
        self.provider_type = provider_type
        self.basemap = basemap

        layout = QVBoxLayout(self)

        # Name input
        name_layout = QHBoxLayout()
        name_label = QLabel(self.tr("Name:"))
        self.name_edit = QLineEdit()
        if self.basemap:
            self.name_edit.setText(self.basemap["name"])
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Tile Type (only for non-WMS providers)
        is_vector = bool(self.basemap and self.basemap.get("tile_type") == "vector")
        if provider_type != "wms":
            tile_type_layout = QHBoxLayout()
            tile_type_label = QLabel(self.tr("Tile Type:"))
            self.tile_type_combo = QComboBox()
            self.tile_type_combo.addItems([self.tr("Raster (XYZ)"), self.tr("Vector")])
            if is_vector:
                self.tile_type_combo.setCurrentIndex(1)
            self.tile_type_combo.currentIndexChanged.connect(self._on_tile_type_changed)
            tile_type_layout.addWidget(tile_type_label)
            tile_type_layout.addWidget(self.tile_type_combo, 1)
            layout.addLayout(tile_type_layout)

        # Source URL input
        url_layout = QHBoxLayout()
        self.url_label = QLabel(
            self.tr("Source URL:") if is_vector else self.tr("URL:")
        )
        self.url_edit = QLineEdit()
        if self.basemap:
            self.url_edit.setText(self.basemap.get("url", ""))
        url_layout.addWidget(self.url_label)
        url_layout.addWidget(self.url_edit)
        layout.addLayout(url_layout)

        # Style URL input (only for vector tiles)
        self.style_url_layout = QHBoxLayout()
        style_url_label = QLabel(self.tr("Style URL:"))
        self.style_url_edit = QLineEdit()
        if self.basemap:
            self.style_url_edit.setText(self.basemap.get("style_url", ""))
        self.style_url_layout.addWidget(style_url_label)
        self.style_url_layout.addWidget(self.style_url_edit)
        layout.addLayout(self.style_url_layout)
        if not is_vector:
            self._set_style_url_visible(False)

        # Layer settings (only show when WMS type)
        if provider_type == "wms":
            layer_group = QGroupBox(self.tr("Layer Settings"))
            layer_layout = QVBoxLayout()

            # Layer name
            layer_name_layout = QHBoxLayout()
            layer_name_label = QLabel(self.tr("Layer Name:"))
            self.layer_name_edit = QLineEdit()
            if self.basemap:
                self.layer_name_edit.setText(self.basemap.get("layer_name", ""))
            layer_name_layout.addWidget(layer_name_label)
            layer_name_layout.addWidget(self.layer_name_edit)
            layer_layout.addLayout(layer_name_layout)

            # Layer title
            layer_title_layout = QHBoxLayout()
            layer_title_label = QLabel(self.tr("Layer Title:"))
            self.layer_title_edit = QLineEdit()
            if self.basemap:
                self.layer_title_edit.setText(self.basemap.get("layer_title", ""))
            layer_title_layout.addWidget(layer_title_label)
            layer_title_layout.addWidget(self.layer_title_edit)
            layer_layout.addLayout(layer_title_layout)

            # CRS
            crs_layout = QHBoxLayout()
            crs_label = QLabel(self.tr("CRS:"))
            self.crs_edit = QLineEdit()
            self.crs_edit.setText(self.tr("EPSG:4326"))  # Default value
            if self.basemap:
                self.crs_edit.setText(self.basemap.get("crs", "EPSG:4326"))
            crs_layout.addWidget(crs_label)
            crs_layout.addWidget(self.crs_edit)
            layer_layout.addLayout(crs_layout)

            # Format
            format_layout = QHBoxLayout()
            format_label = QLabel(self.tr("Format:"))
            self.format_combo = QComboBox()
            self.format_combo.addItems([
                self.tr("image/png"),
                self.tr("image/jpeg"),
                self.tr("image/tiff"),
            ])
            if self.basemap:
                self.format_combo.setCurrentText(
                    self.basemap.get("format", "image/png")
                )
            format_layout.addWidget(format_label)
            format_layout.addWidget(self.format_combo)
            layer_layout.addLayout(format_layout)

            layer_group.setLayout(layer_layout)
            layout.addWidget(layer_group)

        # Tag — store English tag values as item data
        tag_layout = QHBoxLayout()
        tag_layout.setSpacing(4)
        tag_label = QLabel(self.tr("Tag:"))
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("", "")
        for tag in ASSIGNABLE_TAGS:
            self.tag_combo.addItem(
                QCoreApplication.translate("BasemapsDialog", tag), tag
            )
        existing_tags = self.basemap.get("tags", []) if self.basemap else []
        if existing_tags:
            self.tag_combo.setCurrentText(
                QCoreApplication.translate("BasemapsDialog", existing_tags[0])
            )
        tag_layout.addWidget(tag_label)
        tag_layout.addWidget(self.tag_combo, 1)
        layout.addLayout(tag_layout)

        # ── Layer Metadata (optional) ─────────────────────────────
        meta_group = QGroupBox(self.tr("Layer Metadata (optional)"))
        meta_layout = QVBoxLayout(meta_group)

        # Website
        website_layout = QHBoxLayout()
        website_label = QLabel(self.tr("Website:"))
        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("https://...")
        if self.basemap:
            self.website_edit.setText(self.basemap.get("website", ""))
        website_layout.addWidget(website_label)
        website_layout.addWidget(self.website_edit)
        meta_layout.addLayout(website_layout)

        # Copyright
        copyright_layout = QHBoxLayout()
        copyright_label = QLabel(self.tr("Copyright:"))
        self.copyright_edit = QLineEdit()
        self.copyright_edit.setPlaceholderText("© ...")
        if self.basemap:
            self.copyright_edit.setText(self.basemap.get("copyright", ""))
        copyright_layout.addWidget(copyright_label)
        copyright_layout.addWidget(self.copyright_edit)
        meta_layout.addLayout(copyright_layout)

        # Terms of Use
        terms_layout = QHBoxLayout()
        terms_label = QLabel(self.tr("Terms of Use:"))
        self.terms_edit = QLineEdit()
        self.terms_edit.setPlaceholderText("https://...")
        if self.basemap:
            self.terms_edit.setText(self.basemap.get("terms_of_use", ""))
        terms_layout.addWidget(terms_label)
        terms_layout.addWidget(self.terms_edit)
        meta_layout.addLayout(terms_layout)

        # Description
        desc_layout = QVBoxLayout()
        desc_label = QLabel(self.tr("Description:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText(self.tr("Brief description of the layer..."))
        if self.basemap:
            self.desc_edit.setText(self.basemap.get("description", ""))
        desc_layout.addWidget(desc_label)
        desc_layout.addWidget(self.desc_edit)
        meta_layout.addLayout(desc_layout)

        layout.addWidget(meta_group)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        _translate_button_box(button_box)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_tile_type_changed(self, index: int) -> None:
        """Toggle visibility of style URL field based on tile type selection."""
        is_vector = index == 1
        self.url_label.setText(self.tr("Source URL:") if is_vector else self.tr("URL:"))
        self._set_style_url_visible(is_vector)

    def _set_style_url_visible(self, visible: bool) -> None:
        """Show or hide the style URL input row."""
        for i in range(self.style_url_layout.count()):
            widget = self.style_url_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)

    def get_data(self):
        selected = self.tag_combo.currentData(user_role) or ""
        tags = [selected] if selected else []

        # Optional layer metadata — only include non-empty values
        metadata = {}
        website = self.website_edit.text().strip()
        if website:
            metadata["website"] = website
        copyright_text = self.copyright_edit.text().strip()
        if copyright_text:
            metadata["copyright"] = copyright_text
        terms = self.terms_edit.text().strip()
        if terms:
            metadata["terms_of_use"] = terms
        desc = self.desc_edit.text().strip()
        if desc:
            metadata["description"] = desc

        if self.provider_type == "wms":
            data = {
                "name": self.name_edit.text(),
                "url": self.url_edit.text(),
                "layer_name": self.layer_name_edit.text(),
                "layer_title": self.layer_title_edit.text(),
                "crs": self.crs_edit.text(),
                "format": self.format_combo.currentText(),
                "tags": tags,
            }
            data.update(metadata)
            return data
        else:
            is_vector = (
                hasattr(self, "tile_type_combo")
                and self.tile_type_combo.currentIndex() == 1
            )
            data = {
                "name": self.name_edit.text(),
                "tile_type": "vector" if is_vector else "raster",
                "url": self.url_edit.text(),
                "tags": tags,
            }
            if is_vector and hasattr(self, "style_url_edit"):
                style_url = self.style_url_edit.text().strip()
                if style_url:
                    data["style_url"] = style_url
            data.update(metadata)
            return data


class TagEditDialog(QDialog):
    """Simple dialog for editing tags on a basemap or WMS/WMTS layer."""

    def __init__(self, parent=None, layer_title: str = "", layer: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Tags"))
        self.setMinimumWidth(320)
        self.layer = layer or {}
        existing_tags = self.layer.get("tags", [])

        layout = QVBoxLayout(self)

        # Layer name label
        name_label = QLabel(f"<b>{layer_title}</b>")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # Tag row
        tag_row = QHBoxLayout()
        tag_row.setSpacing(4)
        tag_row.addWidget(QLabel(self.tr("Tag:")))

        self.tag_combo = QComboBox()
        self.tag_combo.addItem("", "")
        for tag in ASSIGNABLE_TAGS:
            self.tag_combo.addItem(
                QCoreApplication.translate("BasemapsDialog", tag), tag
            )
        if existing_tags:
            self.tag_combo.setCurrentText(
                QCoreApplication.translate("BasemapsDialog", existing_tags[0])
            )
        tag_row.addWidget(self.tag_combo, 1)
        layout.addLayout(tag_row)

        # Save mode radio group
        save_group = QGroupBox(self.tr("Save tags to"))
        save_layout = QVBoxLayout(save_group)
        self.btn_overrides = QRadioButton(
            self.tr("Sidecar file (recommended, safer for updates)")
        )
        self.btn_direct = QRadioButton(self.tr("Original file"))
        self.btn_direct.setToolTip(
            self.tr(
                "Warning: edits in provider config files "
                "will be lost when the plugin is updated."
            )
        )
        save_layout.addWidget(self.btn_overrides)
        save_layout.addWidget(self.btn_direct)
        layout.addWidget(save_group)

        # Restore last-used preference
        last_mode = QSettings("Basemaps", "Basemaps").value(
            "tag_save_preference", "overrides"
        )
        if last_mode == "direct":
            self.btn_direct.setChecked(True)
        else:
            self.btn_overrides.setChecked(True)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        _translate_button_box(button_box)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_tags(self) -> list[str]:
        """Return the list of selected tags."""
        selected = self.tag_combo.currentData(user_role) or ""
        return [selected] if selected else []

    def get_save_mode(self) -> str:
        """Return the selected save mode: 'overrides' or 'direct'."""
        return "direct" if self.btn_direct.isChecked() else "overrides"

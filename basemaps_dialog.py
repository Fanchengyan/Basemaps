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

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from qgis.core import QgsApplication, QgsDataSourceUri, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import (
    QT_VERSION_STR,
    QCoreApplication,
    QSize,
    Qt,
)
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QListWidget,
    QMenu,
    QPushButton,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import config_loader
from .messageTool import Logger, MessageBox
from .ui import IconBasemaps, UIBasemapsBase
from .ui.basemap_delegate import BasemapCardDelegate
from .preview_manager import PreviewManager
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
OVERLAY_LABELS_TAG = "Overlay/Labels"

AVAILABLE_TAGS = [
    "All",
    "Satellite",
    "Streets",
    "Terrain",
    "Thematic",
    OVERLAY_TAG,
    "Overlay/Hydrography",
    "Overlay/Transportation",
    OVERLAY_LABELS_TAG,
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
    OVERLAY_LABELS_TAG,
    "Overlay/Boundaries",
]

TOKEN_PARAM_OPTIONS = ["apikey", "key", "api_key", "access_token", "token", "tk"]
DEFAULT_TOKEN_PARAM = "apikey"


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
        self.basemap_delegate = BasemapCardDelegate(self)
        for grid_view in [self.listBasemapsGrid, self.listWmsLayersGrid]:
            grid_view.setItemDelegate(self.basemap_delegate)
            grid_view.setViewMode(QListWidget.IconMode)
            grid_view.setResizeMode(QListWidget.Adjust)
            grid_view.setWrapping(True)
            grid_view.setSpacing(10)
            grid_view.setWordWrap(True)
            # Ensure cards are centered
            grid_view.setMovement(QListWidget.Static)
            # Enable hover effects
            grid_view.setMouseTracking(True)

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
        self.btnEditWmsLayer.clicked.connect(self.edit_wms_layer_tags)
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

        # Setup tag filter combo
        self.tagFilterCombo.addItems(AVAILABLE_TAGS)
        self.tagFilterCombo.setCurrentIndex(0)
        self.tagFilterCombo.currentTextChanged.connect(self._on_tag_changed)

        # Connect search boxes
        self.searchBasemaps.textChanged.connect(self._on_xyz_search_changed)
        self.searchWmsLayers.textChanged.connect(self._on_wms_search_changed)

        # Load configurations
        self.load_default_basemaps()
        self.load_user_basemaps()

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate("BasemapsDialog", message)

    def _on_tag_changed(self, tag_name: str) -> None:
        """Handle tag filter combo change."""
        self._active_tag = tag_name
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
        self, provider: dict[str, Any], suffix: str = " (Custom)"
    ) -> dict[str, Any]:
        """Create user copy of a provider.

        Parameters
        ----------
        provider : dict[str, Any]
            Source provider to duplicate
        suffix : str
            Suffix to add to name (default: " (Custom)")

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
                        raise Exception("No YAML configuration file found in ZIP")

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
        default_filename = "providers.zip"
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
                        provider["name"] = f"{provider['name']} (Custom)"
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
                    item = QListWidgetItem(provider["name"])
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

    def add_provider(self):
        dialog = ProviderInputDialog(self)
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
        token = ""
        token_param = DEFAULT_TOKEN_PARAM
        if current_provider:
            provider_data = current_provider.data(user_role)
            if provider_data:
                provider = provider_data.get("data", {})
                token = provider.get("token", "")
                token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)

        for item in selected_items:
            basemap = item.data(user_role)
            if not basemap:
                continue

            try:
                url = self._append_token(basemap["url"], token, token_param)
                name = basemap["name"]

                # Create XYZ layer
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
        current_item = self.listProviders.currentItem()
        if not current_item:
            self.listBasemaps.clear()
            self.listBasemapsGrid.clear()
            self.btnEditBasemap.setEnabled(False)
            self.btnRemoveBasemap.setEnabled(False)
            self.btnEditProvider.setEnabled(False)
            self.btnRemoveProvider.setEnabled(False)
            return

        # Cancel any pending preview requests from previous provider
        self.preview_manager.cleanup()

        provider_data = current_item.data(user_role)
        if not provider_data or "data" not in provider_data:
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

        # Update basemap list and grid
        self.listBasemaps.clear()
        self.listBasemapsGrid.clear()

        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
        for basemap in provider_data["data"].get("basemaps", []):
            if isinstance(basemap, dict) and "name" in basemap and "url" in basemap:
                # List item
                item = QListWidgetItem(basemap["name"])
                item.setIcon(provider_icon)
                item.setData(user_role, basemap)
                self.listBasemaps.addItem(item)

                # Grid item
                grid_item = QListWidgetItem(basemap["name"])
                grid_item.setData(user_role, basemap)
                grid_item.setData(Qt.UserRole + 10, provider_icon)
                grid_item.setToolTip(basemap["name"])  # Show full name on hover
                self.listBasemapsGrid.addItem(grid_item)

                # Request preview with token in URL
                is_default = self._is_default_provider(provider)
                preview_url = self._append_token(basemap["url"], token, token_param)
                self.preview_manager.request_preview(
                    provider["name"],
                    basemap["name"],
                    preview_url,
                    "xyz",
                    None,
                    is_default,
                )

        # Disable edit/remove/add basemap buttons for default providers
        # Disable remove provider button for default providers
        is_default = self._is_default_provider(provider)
        self.btnEditBasemap.setEnabled(not is_default)
        self.btnRemoveBasemap.setEnabled(not is_default)
        self.btnAddBasemap.setEnabled(not is_default)
        self.btnEditProvider.setEnabled(not is_default)
        self.btnRemoveProvider.setEnabled(not is_default)

        # Apply tag filter to the newly loaded basemaps
        self._apply_tag_filter()

    def on_wms_provider_changed(self):
        """
        Update layer tree when WMS provider changed.

        Build hierarchical tree structure showing:
        - Layer name (top level)
          - CRS options (second level)
            - Format options (third level)
        """
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            self.treeWmsLayers.clear()
            self.btnEditWmsProvider.setEnabled(False)
            self.btnRemoveWmsProvider.setEnabled(False)
            return

        provider_data = current_item.data(user_role)
        if not provider_data:
            return

        # Set tree icon size
        self.treeWmsLayers.setIconSize(QSize(15, 15))

        # Get provider icon
        provider = provider_data["data"]
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
        provider_icon = IconBasemaps
        if "icon" in provider:
            icon_file = self.icons_dir / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))

        # Update layer tree with hierarchical structure
        self.treeWmsLayers.clear()
        self.listWmsLayersGrid.clear()
        for layer in provider_data["data"].get("layers", []):
            # Use layer_title as display name, if not available use layer_name
            display_name = layer.get(
                "layer_title", layer.get("layer_name", "Unknown Layer")
            )

            # Create top-level item for layer
            layer_item = QTreeWidgetItem([display_name])
            layer_item.setIcon(0, provider_icon)
            self.treeWmsLayers.addTopLevelItem(layer_item)

            # Grid item
            grid_item = QListWidgetItem(display_name)
            grid_item.setData(user_role, layer)
            grid_item.setData(Qt.UserRole + 10, provider_icon)
            grid_item.setToolTip(display_name)  # Show full name on hover
            self.listWmsLayersGrid.addItem(grid_item)

            # Request preview for WMS layer (using queue system to prevent overload)
            service_type = provider.get("service_type", "wms")
            is_default = self._is_default_provider(provider)
            preview_url = self._append_token(provider["url"], token, token_param)
            self.preview_manager.request_preview(
                provider["name"],
                display_name,
                preview_url,
                service_type,
                layer,
                is_default,
            )

            # Get available CRS, formats, and styles
            crs_list = layer.get("crs", [])
            format_list = layer.get("format", [])
            style_list = layer.get("styles", [""])

            # If only one option for each parameter, create leaf item directly
            if len(crs_list) <= 1 and len(format_list) <= 1:
                # Store complete layer data in the layer item itself
                layer_item.setData(0, user_role, layer)
                continue

            # For multi-parameter layers, store default config in parent node
            # This allows loading by clicking the parent node (uses first CRS)
            # Note: service_type is at provider level, not layer level
            provider_service_type = provider.get("service_type", "wms")
            layer_tags = layer.get("tags", [])
            default_config = {
                "layer_name": layer.get("layer_name"),
                "layer_title": layer.get("layer_title"),
                "crs": [crs_list[0]] if crs_list else [],
                "format": format_list if format_list else [],
                "styles": style_list,
                "service_type": provider_service_type,
                "tags": layer_tags,
            }
            layer_item.setData(0, user_role, default_config)

            # Create second-level items for CRS options
            for crs in crs_list:
                crs_item = QTreeWidgetItem([crs])
                layer_item.addChild(crs_item)

                # If multiple formats available, create third level
                if len(format_list) > 1:
                    for fmt in format_list:
                        # Create format item with all layer data
                        format_item = QTreeWidgetItem([fmt])

                        # Store complete layer configuration
                        layer_config = {
                            "layer_name": layer.get("layer_name"),
                            "layer_title": layer.get("layer_title"),
                            "crs": [crs],
                            "format": [fmt],
                            "styles": style_list,
                            "service_type": provider_service_type,
                            "tags": layer_tags,
                        }
                        format_item.setData(0, user_role, layer_config)
                        crs_item.addChild(format_item)
                else:
                    # Only one format, store data in CRS item
                    layer_config = {
                        "layer_name": layer.get("layer_name"),
                        "layer_title": layer.get("layer_title"),
                        "crs": [crs],
                        "format": format_list,
                        "styles": style_list,
                        "service_type": provider_service_type,
                        "tags": layer_tags,
                    }
                    crs_item.setData(0, user_role, layer_config)

        # Disable remove provider and edit tags buttons for default providers
        is_default = self._is_default_provider(provider)
        self.btnEditWmsProvider.setEnabled(not is_default)
        self.btnRemoveWmsProvider.setEnabled(not is_default)
        self.btnEditWmsLayer.setEnabled(not is_default)

        # Apply tag filter to the newly loaded WMS layers
        self._apply_tag_filter()

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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
            provider_data.update(
                {
                    "type": "xyz",
                    "basemaps": [],  # Initialize empty basemap list
                    "created_at": __import__("time").time(),  # Add creation timestamp
                }
            )

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

            # Delete provider files
            for provider in providers_to_delete:
                config_loader.delete_provider_file(
                    self.resources_dir, provider, prefix="user"
                )

            # Delete provider from data
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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

    def edit_xyz_basemap(self):
        """edit XYZ basemap"""
        current_provider = self.listProviders.currentItem()
        current_basemap = self.listBasemaps.currentItem()
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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
            provider_data.update(
                {
                    "type": "wms",
                    "layers": [],  # Initialize empty layer list
                    "created_at": __import__("time").time(),  # Add creation timestamp
                }
            )

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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
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
        url = provider["url"]
        token = provider.get("token", "")
        token_param = provider.get("token_param", DEFAULT_TOKEN_PARAM)
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
        """
        Load a WMS layer to QGIS.

        Parameters
        ----------
        url : str
            The WMS service URL.
        layer_data : dict
            Layer information dictionary.
        """
        # Build WMS parameters
        params = {
            "url": url,
            "layers": layer_data["layer_name"],
            "format": layer_data["format"][0],
            "crs": layer_data["crs"][0],
            "styles": layer_data["styles"][0] if layer_data["styles"] else "",
        }

        # Build URI using QgsDataSourceUri
        uri = QgsDataSourceUri()
        for key, value in params.items():
            uri.setParam(key, value)

        # Create layer
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
        """
        Load a WMTS layer to QGIS.

        Parameters
        ----------
        url : str
            The WMTS service URL.
        layer_data : dict
            Layer information dictionary.
        """
        # Build WMTS URI
        # QGIS uses a specific format for WMTS connections
        uri = QgsDataSourceUri()
        uri.setParam("url", url)
        uri.setParam("layers", layer_data["layer_name"])

        # Set tile matrix set (CRS)
        if layer_data.get("crs"):
            uri.setParam("tileMatrixSet", layer_data["crs"][0])

        # Set format
        if layer_data.get("format"):
            uri.setParam("format", layer_data["format"][0])

        # Set style
        if layer_data.get("styles"):
            uri.setParam("styles", layer_data["styles"][0])
        else:
            uri.setParam("styles", "")

        # Log the URI for debugging
        encoded_uri = str(uri.encodedUri(), "utf-8")
        Logger.info(f"Loading WMTS layer with URI: {encoded_uri}")

        # Create layer with 'wms' provider (QGIS uses wms provider for WMTS too)
        layer = QgsRasterLayer(encoded_uri, layer_data["layer_title"], "wms")

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        else:
            # Get detailed error info from the layer
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
            Logger.warning("Fetch completed but context was lost")
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
            new_provider.update(
                {
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "service_type": detected_type,
                    "url": url,
                    "layers": result.layers,
                }
            )
            self.providers_data.append(new_provider)
            selected_provider_name = new_provider["name"]
        else:
            # Update existing user provider
            self.providers_data[provider_index].update(
                {
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "service_type": detected_type,
                    "url": url,
                    "layers": result.layers,
                }
            )
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
        duplicate_action = menu.addAction("Duplicate as User Provider")

        exec = menu.exec_ if hasattr(menu, "exec_") else menu.exec
        action = exec(self.listProviders.mapToGlobal(position))

        if action == duplicate_action:
            self.duplicate_xyz_provider()

    def show_xyz_basemap_context_menu(self, position):
        current_item = self.listBasemaps.currentItem()
        if not current_item:
            return

        menu = QMenu()
        edit_action = menu.addAction("Edit")

        exec = menu.exec_ if hasattr(menu, "exec_") else menu.exec
        action = exec(self.listBasemaps.mapToGlobal(position))

        if action == edit_action:
            # Check if editing a default provider's basemap (disabled)
            current_provider = self.listProviders.currentItem()
            if current_provider:
                provider_data = current_provider.data(user_role)
                if provider_data and "data" in provider_data:
                    if self._is_default_provider(provider_data["data"]):
                        MessageBox.warning(
                            self.tr("Default basemaps cannot be edited."),
                            self.tr("Warning"),
                            self,
                        )
                        return
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
        duplicate_action = menu.addAction("Duplicate as User Provider")

        exec = menu.exec_ if hasattr(menu, "exec_") else menu.exec
        action = exec(self.listWmsProviders.mapToGlobal(position))

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

        provider = provider_data["data"]
        layer_name = layer.get("layer_name")
        if not layer_name:
            Logger.critical("EDIT_LAYER: Selected layer is missing layer_name")
            return

        layer_title = layer.get("layer_title", layer.get("layer_name", "Unknown"))
        dialog = TagEditDialog(self, layer_title, layer)
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        if exec_result == dialog_accepted:
            new_tags = dialog.get_tags()

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

            # Save user config if it's a user provider
            if not self._is_default_provider(provider):
                Logger.info("Saving user config for WMS layer tags")
                self.save_user_config()

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

        exec_menu = menu.exec_ if hasattr(menu, "exec_") else menu.exec
        action = exec_menu(self.treeWmsLayers.mapToGlobal(position))

        if action == edit_action:
            # Check if editing a default provider's layer (disabled)
            current_provider = self.listWmsProviders.currentItem()
            if current_provider:
                provider_data = current_provider.data(user_role)
                if provider_data and "data" in provider_data:
                    if self._is_default_provider(provider_data["data"]):
                        MessageBox.warning(
                            self.tr("Default WMS layers cannot be edited."),
                            self.tr("Warning"),
                            self,
                        )
                        return
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

        exec_menu = menu.exec_ if hasattr(menu, "exec_") else menu.exec
        action = exec_menu(self.listWmsLayersGrid.mapToGlobal(position))

        if action == edit_action:
            # Check if editing a default provider's layer (disabled)
            current_provider = self.listWmsProviders.currentItem()
            if current_provider:
                provider_data = current_provider.data(user_role)
                if provider_data and "data" in provider_data:
                    if self._is_default_provider(provider_data["data"]):
                        MessageBox.warning(
                            self.tr("Default WMS layers cannot be edited."),
                            self.tr("Warning"),
                            self,
                        )
                        return
            self.edit_wms_layer_tags()

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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        if exec_result == dialog_accepted:
            # Get edited data
            new_data = dialog.get_data()
            new_data["type"] = "xyz"
            new_data["basemaps"] = provider.get("basemaps", [])

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
        exec_result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        if exec_result == dialog_accepted:
            new_data = dialog.get_data()
            new_data["type"] = "wms"
            new_data["layers"] = provider.get("layers", [])
            new_data["url"] = dialog.url_edit.text()

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
                    item.setData(Qt.DecorationRole, pixmap)
                    # Trigger repaint
                    grid_view.update()

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
        if not token:
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


class ProviderInputDialog(QDialog):
    def __init__(self, parent=None, provider=None, provider_type="xyz"):
        super().__init__(parent)
        self.setWindowTitle("Add Provider")
        self.plugin_dir = Path(__file__).parent
        self.provider_type = provider_type

        layout = QVBoxLayout(self)

        # Name input
        name_layout = QHBoxLayout()
        name_label = QLabel("Name:")
        self.name_edit = QLineEdit()
        if provider:
            self.name_edit.setText(provider["name"])
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # Icon input
        icon_layout = QHBoxLayout()
        icon_label = QLabel("Icon:")
        self.icon_edit = QLineEdit()
        if provider:
            self.icon_edit.setText(provider.get("icon", ""))
        self.icon_button = QPushButton("Browse...")
        self.icon_button.clicked.connect(self.browse_icon)
        icon_layout.addWidget(icon_label)
        icon_layout.addWidget(self.icon_edit)
        icon_layout.addWidget(self.icon_button)
        layout.addLayout(icon_layout)

        # URL input (only show when WMS type)
        if provider_type == "wms":
            url_layout = QHBoxLayout()
            url_label = QLabel("URL:")
            self.url_edit = QLineEdit()
            if provider:
                self.url_edit.setText(provider.get("url", ""))
            url_layout.addWidget(url_label)
            url_layout.addWidget(self.url_edit)
            layout.addLayout(url_layout)

        self.token_auth_widget = TokenAuthWidget(self, provider)
        layout.addWidget(self.token_auth_widget)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def browse_icon(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Icon File",
            "",
            "Image Files (*.png *.jpg *.svg *.ico);;All Files (*)",
        )
        if file_path:
            self.icon_edit.setText(file_path)

    def get_data(self):
        data = {
            "name": self.name_edit.text(),
            "icon": self.icon_edit.text() or "ui/icon.svg",
        }

        # Token
        data.update(self.token_auth_widget.get_data())

        if self.provider_type == "wms":
            data["url"] = self.url_edit.text()

        return data


class TokenAuthWidget(QGroupBox):
    """Provider token and token-parameter input widget."""

    def __init__(self, parent: QDialog | None = None, provider: dict | None = None):
        super().__init__("Authentication (Optional)", parent)

        provider_data = provider or {}
        layout = QVBoxLayout(self)

        token_layout = QHBoxLayout()
        token_label = QLabel("Token:")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Optional API token / key")
        self.token_edit.setText(provider_data.get("token", ""))
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.token_edit)
        layout.addLayout(token_layout)

        token_param_layout = QHBoxLayout()
        token_param_label = QLabel("Token Parameter:")
        self.token_param_combo = QComboBox()
        self.token_param_combo.setEditable(True)
        self.token_param_combo.addItems(TOKEN_PARAM_OPTIONS)
        self.token_param_combo.setCurrentText(
            provider_data.get("token_param", DEFAULT_TOKEN_PARAM)
        )
        token_param_layout.addWidget(token_param_label)
        token_param_layout.addWidget(self.token_param_combo)
        layout.addLayout(token_param_layout)

    def get_data(self) -> dict[str, str]:
        """Return token-related provider fields."""
        token = self.token_edit.text().strip()
        if not token:
            return {}

        token_param = self.token_param_combo.currentText().strip()
        return {
            "token": token,
            "token_param": token_param or DEFAULT_TOKEN_PARAM,
        }


class BasemapInputDialog(QDialog):
    def __init__(self, parent=None, basemap=None, provider_type=None):
        super().__init__(parent)
        self.setWindowTitle("Add Basemap")
        self.provider_type = provider_type
        self.basemap = basemap

        layout = QVBoxLayout(self)

        # Name input
        name_layout = QHBoxLayout()
        name_label = QLabel("Name:")
        self.name_edit = QLineEdit()
        if self.basemap:
            self.name_edit.setText(self.basemap["name"])
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        # URL input
        url_layout = QHBoxLayout()
        url_label = QLabel("URL:")
        self.url_edit = QLineEdit()
        if self.basemap:
            self.url_edit.setText(self.basemap["url"])
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_edit)
        layout.addLayout(url_layout)

        # Layer settings (only show when WMS type)
        if provider_type == "wms":
            layer_group = QGroupBox("Layer Settings")
            layer_layout = QVBoxLayout()

            # Layer name
            layer_name_layout = QHBoxLayout()
            layer_name_label = QLabel("Layer Name:")
            self.layer_name_edit = QLineEdit()
            if self.basemap:
                self.layer_name_edit.setText(self.basemap.get("layer_name", ""))
            layer_name_layout.addWidget(layer_name_label)
            layer_name_layout.addWidget(self.layer_name_edit)
            layer_layout.addLayout(layer_name_layout)

            # Layer title
            layer_title_layout = QHBoxLayout()
            layer_title_label = QLabel("Layer Title:")
            self.layer_title_edit = QLineEdit()
            if self.basemap:
                self.layer_title_edit.setText(self.basemap.get("layer_title", ""))
            layer_title_layout.addWidget(layer_title_label)
            layer_title_layout.addWidget(self.layer_title_edit)
            layer_layout.addLayout(layer_title_layout)

            # CRS
            crs_layout = QHBoxLayout()
            crs_label = QLabel("CRS:")
            self.crs_edit = QLineEdit()
            self.crs_edit.setText("EPSG:4326")  # Default value
            if self.basemap:
                self.crs_edit.setText(self.basemap.get("crs", "EPSG:4326"))
            crs_layout.addWidget(crs_label)
            crs_layout.addWidget(self.crs_edit)
            layer_layout.addLayout(crs_layout)

            # Format
            format_layout = QHBoxLayout()
            format_label = QLabel("Format:")
            self.format_combo = QComboBox()
            self.format_combo.addItems(["image/png", "image/jpeg", "image/tiff"])
            if self.basemap:
                self.format_combo.setCurrentText(
                    self.basemap.get("format", "image/png")
                )
            format_layout.addWidget(format_label)
            format_layout.addWidget(self.format_combo)
            layer_layout.addLayout(format_layout)

            layer_group.setLayout(layer_layout)
            layout.addWidget(layer_group)

        # Tag
        tag_layout = QHBoxLayout()
        tag_layout.setSpacing(4)
        tag_label = QLabel("Tag:")
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("")
        for tag in ASSIGNABLE_TAGS:
            self.tag_combo.addItem(tag)
        existing_tags = self.basemap.get("tags", []) if self.basemap else []
        if existing_tags:
            self.tag_combo.setCurrentText(existing_tags[0])
        tag_layout.addWidget(tag_label)
        tag_layout.addWidget(self.tag_combo, 1)
        layout.addLayout(tag_layout)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_data(self):
        selected = self.tag_combo.currentText()
        tags = [selected] if selected else []
        if self.provider_type == "wms":
            return {
                "name": self.name_edit.text(),
                "url": self.url_edit.text(),
                "layer_name": self.layer_name_edit.text(),
                "layer_title": self.layer_title_edit.text(),
                "crs": self.crs_edit.text(),
                "format": self.format_combo.currentText(),
                "tags": tags,
            }
        else:
            return {
                "name": self.name_edit.text(),
                "url": self.url_edit.text(),
                "tags": tags,
            }


class TagEditDialog(QDialog):
    """Simple dialog for editing tags on a WMS/WMTS layer."""

    def __init__(self, parent=None, layer_title: str = "", layer: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Tags"))
        self.setMinimumWidth(300)
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
        self.tag_combo.addItem("")
        for tag in ASSIGNABLE_TAGS:
            self.tag_combo.addItem(tag)
        if existing_tags:
            self.tag_combo.setCurrentText(existing_tags[0])
        tag_row.addWidget(self.tag_combo, 1)
        layout.addLayout(tag_row)

        # Buttons
        button_box = QDialogButtonBox(button_ok | button_cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_tags(self) -> list[str]:
        """Return the list of selected tags."""
        selected = self.tag_combo.currentText()
        return [selected] if selected else []

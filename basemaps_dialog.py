# Copyright (C) 2024  Chengyan (Fancy) Fan 

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import json
from pathlib import Path

from owslib.wms import WebMapService
from qgis.core import QgsDataSourceUri, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import QCoreApplication, QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
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
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QMenu,
)

from .ui import UIBasemapsBase


class BasemapsDialog(QDialog, UIBasemapsBase):
    def __init__(self, iface, parent=None):
        super(BasemapsDialog, self).__init__(parent)
        self.iface = iface
        self.setupUi(self)
        self.providers_data = []
        self.user_config_path = (
            Path(__file__).parent / "resources" / "user_basemaps.json"
        )

        # 设置所有列表为多选模式
        self.listProviders.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listBasemaps.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listWmsProviders.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listWmsLayers.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # 设置右键菜单
        self.listProviders.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listProviders.customContextMenuRequested.connect(self.show_xyz_provider_context_menu)
        
        self.listBasemaps.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listBasemaps.customContextMenuRequested.connect(self.show_xyz_basemap_context_menu)
        
        self.listWmsProviders.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listWmsProviders.customContextMenuRequested.connect(self.show_wms_provider_context_menu)

        # Connect signals and slots
        self.btnLoadJson.clicked.connect(self.import_config)
        self.btnSaveJson.clicked.connect(self.save_json)

        # XYZ connections
        self.btnAddProvider.clicked.connect(self.add_xyz_provider)
        self.btnRemoveProvider.clicked.connect(self.remove_xyz_provider)
        self.btnAddBasemap.clicked.connect(self.add_xyz_basemap)
        self.btnEditBasemap.clicked.connect(self.edit_xyz_basemap)
        self.btnRemoveBasemap.clicked.connect(self.remove_xyz_basemap)
        self.btnLoadBasemap.clicked.connect(self.load_xyz_basemap)
        self.listProviders.itemSelectionChanged.connect(self.on_provider_changed)

        # WMS connections
        self.btnAddWmsProvider.clicked.connect(self.add_wms_provider)
        self.btnRemoveWmsProvider.clicked.connect(self.remove_wms_provider)
        self.btnRefreshWmsLayers.clicked.connect(self.refresh_wms_layers)
        self.btnLoadWmsLayer.clicked.connect(self.load_wms_layer)
        self.listWmsProviders.itemSelectionChanged.connect(self.on_wms_provider_changed)

        # Load configurations
        self.load_default_basemaps()
        self.load_user_basemaps()

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate("BasemapsDialog", message)

    def load_default_basemaps(self):
        default_json = Path(__file__).parent / "resources" / "default_basemaps.json"
        if default_json.exists():
            try:
                with open(default_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.providers_data = data.get("providers", []) 
                self.update_providers_list()
            except Exception as e:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Failed to load default configuration: {}").format(str(e)),
                )

    def load_user_basemaps(self):
        if self.user_config_path.exists():
            try:
                with open(self.user_config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.providers_data.extend(data.get("providers", []))
                self.update_providers_list()
            except Exception as e:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Failed to save user configuration: {}").format(str(e)),
                )

    def import_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Import Configuration File"),
            "",
            self.tr("ZIP files (*.zip);;JSON files (*.json)"),
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

                    # Find JSON file
                    json_files = list(Path(temp_dir).glob("*.json"))
                    if not json_files:
                        raise Exception("No JSON file found in ZIP")

                    # Load JSON data
                    with open(json_files[0], "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # Copy icon files
                    icons_dir = Path(temp_dir) / "icons"
                    if icons_dir.exists():
                        target_icons_dir = Path(__file__).parent / "resources" / "icons"
                        target_icons_dir.mkdir(parents=True, exist_ok=True)
                        for icon_file in icons_dir.glob("*"):
                            shutil.copy2(icon_file, target_icons_dir)

                    # Update data
                    self.providers_data.extend(data.get("providers", []))
                    self.update_providers_list()
                    self.save_user_config()

            except Exception as e:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Failed to import ZIP file: {}").format(str(e)),
                )
        else:
            self.load_basemaps_from_file(file_path)
            self.save_user_config()

    def save_json(self):
        """Export configuration to ZIP file"""
        # Check if any items are selected
        selected_xyz_items = self.listProviders.selectedItems()
        selected_wms_items = self.listWmsProviders.selectedItems()
        
        if not selected_xyz_items and not selected_wms_items:
            # Ask user if they want to export all user-defined providers
            reply = QMessageBox.question(
                self,
                self.tr("Export Configuration"),
                self.tr("No providers selected. Do you want to export all user-defined providers?"),
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
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
                # Find separator index
                separator_index = next(
                    (i for i, p in enumerate(self.providers_data) if p.get("type") == "separator"),
                    -1
                )
                # Export all user-defined providers
                providers = self.providers_data[separator_index + 1:] if separator_index >= 0 else []
                for provider in providers:
                    if provider.get("type") != "separator":
                        providers_to_export.append(provider)
                        if "icon" in provider:
                            icon_files.add(provider["icon"])
            else:
                # Process selected XYZ providers
                for item in selected_xyz_items:
                    provider_data = item.data(Qt.UserRole)
                    if provider_data and provider_data.get("data"):
                        provider = provider_data["data"]
                        if provider.get("type") != "separator":
                            providers_to_export.append(provider)
                            if "icon" in provider:
                                icon_files.add(provider["icon"])
                
                # Process selected WMS providers
                for item in selected_wms_items:
                    provider_data = item.data(Qt.UserRole)
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
                # Save JSON file
                json_path = Path(temp_dir) / "providers.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"providers": providers_to_export},
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )

                # Create ZIP file
                with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    # Add JSON file
                    zipf.write(json_path, "providers.json")

                    # Add icon files
                    for icon_path in icon_files:
                        if icon_path.startswith("icons/"):
                            full_path = Path(__file__).parent / "resources" / icon_path
                            if full_path.exists():
                                zipf.write(full_path, icon_path)

            QMessageBox.information(
                self,
                self.tr("Success"),
                self.tr("Configuration saved successfully."),
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to save configuration: {}").format(str(e)),
            )

    def load_basemaps_from_file(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

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
                        existing_providers[provider["name"]]["basemaps"].extend(
                            provider["basemaps"]
                        )
                else:
                    # Add new provider
                    self.providers_data.append(provider)

            self.update_providers_list()

        except Exception as e:
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to load configuration file: {}").format(str(e)),
            )

    def save_user_config(self):
        """保存用户配置到文件"""
        try:
            # Find separator index
            separator_index = next(
                (i for i, p in enumerate(self.providers_data) if p.get("type") == "separator"),
                -1
            )
            
            # Save user configuration after separator
            user_data = {
                "providers": self.providers_data[separator_index + 1:] if separator_index >= 0 else []
            }
            
            with open(self.user_config_path, "w", encoding="utf-8") as f:
                json.dump(user_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to save configuration: {}").format(str(e)),
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
            return QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # Add providers to corresponding lists
        for i, provider in enumerate(self.providers_data):
            # If separator, add non-selectable separator item
            if provider.get("type") == "separator":
                for list_widget in [self.listProviders, self.listWmsProviders]:
                    item = QListWidgetItem(provider["name"])
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                    list_widget.addItem(item)
                continue

            # Create icon
            if "icon" in provider:
                icon_file = Path(__file__).parent / "resources" / provider["icon"]
                provider_icon = create_scaled_icon(icon_file)
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

            # Add to different lists based on type
            if provider.get("type") == "wms":
                item = QListWidgetItem(provider["name"])
                item.setIcon(provider_icon)
                item.setData(Qt.UserRole, {"index": i, "data": provider})
                self.listWmsProviders.addItem(item)
            else:  # xyz type
                # Ensure provider has basemaps field
                if "basemaps" not in provider:
                    provider["basemaps"] = []
                
                item = QListWidgetItem(provider["name"])
                item.setIcon(provider_icon)
                item.setData(Qt.UserRole, {"index": i, "data": provider})
                self.listProviders.addItem(item)

    def add_provider(self):
        dialog = ProviderInputDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                QMessageBox.warning(
                    self,
                    self.tr("Warning"),
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                )
                return

            self.providers_data.append(provider_data)
            self.update_providers_list()
            self.save_user_config()

    def remove_provider(self):
        selected_items = self.listProviders.selectedItems()
        if not selected_items:
            return

        # Read default provider count
        try:
            default_count = len(
                json.loads(
                    (
                        Path(__file__).parent / "resources" / "default_basemaps.json"
                    ).read_text(encoding="utf-8")
                ).get("providers", [])
            )
        except Exception:
            default_count = 0

        # Check if default providers are selected
        default_selected = any(
            item.data(Qt.UserRole)["index"] < default_count for item in selected_items
        )
        if default_selected:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Default providers cannot be removed."),
            )
            return

        # Get provider names to delete
        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove providers: "{}"?').format(
                names_str
            ),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Collect indices of providers to remove
            indices_to_remove = [
                item.data(Qt.UserRole)["index"] for item in selected_items
            ]
            self.providers_data = [
                p
                for i, p in enumerate(self.providers_data)
                if i not in indices_to_remove
            ]
            self.update_providers_list()
            self.save_user_config()

    def add_basemap(self):
        current_item = self.listProviders.currentItem()
        if not current_item:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select a provider first."),
            )
            return

        dialog = BasemapInputDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            provider_data = current_item.data(Qt.UserRole)
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
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def edit_basemap(self):
        current_provider = self.listProviders.currentItem()
        current_basemap = self.listBasemaps.currentItem()
        if not current_provider or not current_basemap:
            return

        provider_data = current_provider.data(Qt.UserRole)
        basemap = current_basemap.data(Qt.UserRole)

        dialog = BasemapInputDialog(self, basemap)
        if dialog.exec_() == QDialog.Accepted:
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
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
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

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove basemaps: "{}"?').format(
                names_str
            ),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            provider_data = current_provider.data(Qt.UserRole)
            # Directly modify providers_data data
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(Qt.UserRole) for item in selected_basemaps]
            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
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
            basemap = item.data(Qt.UserRole)
            self.load_xyz_basemap(basemap)

    def load_xyz_basemap(self):
        selected_items = self.listBasemaps.selectedItems()
        if not selected_items:
            return

        for item in selected_items:
            basemap = item.data(Qt.UserRole)
            if not basemap:
                continue

            try:
                url = basemap["url"]
                name = basemap["name"]

                # Create XYZ layer
                uri = f"type=xyz&url={url}"
                layer = QgsRasterLayer(uri, name, "wms")

                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                else:
                    QMessageBox.critical(
                        self,
                        self.tr("Error"),
                        self.tr("Failed to load basemap: {}").format(name),
                    )
            except (KeyError, TypeError) as e:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Invalid basemap data: {}").format(str(e)),
                )

    def on_provider_changed(self):
        """update basemap list"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            self.listBasemaps.clear()
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data or "data" not in provider_data:
            return

        # Set basemap list icon size
        self.listBasemaps.setIconSize(QSize(15, 15))

        # Get provider icon
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # Update basemap list
        self.listBasemaps.clear()
        for basemap in provider_data["data"].get("basemaps", []):
            if (
                isinstance(basemap, dict) and "name" in basemap and "url" in basemap
            ):
                item = QListWidgetItem(basemap["name"])
                item.setIcon(provider_icon)
                item.setData(Qt.UserRole, basemap)
                self.listBasemaps.addItem(item)

    def on_wms_provider_changed(self):
        """update layer list when WMS provider changed"""
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            self.listWmsLayers.clear()
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data:
            return

        # Set layer list icon size
        self.listWmsLayers.setIconSize(QSize(15, 15))

        # Get provider icon
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # Update layer list
        self.listWmsLayers.clear()
        for layer in provider_data["data"].get("layers", []):
            # Use layer_title as display name, if not available use layer_name
            display_name = layer.get(
                "layer_title", layer.get("layer_name", "Unknown Layer")
            )
            item = QListWidgetItem(display_name)
            item.setIcon(provider_icon)
            item.setData(Qt.UserRole, layer)
            self.listWmsLayers.addItem(item)

    def update_basemaps_list(self):
        """update basemap list"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data:
            return

        # Set basemap list icon size
        self.listBasemaps.setIconSize(QSize(15, 15))

        # Get provider icon
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # Update basemap list
        self.listBasemaps.clear()
        for basemap in provider_data["data"]["basemaps"]:
            item = QListWidgetItem(basemap["name"])
            item.setIcon(provider_icon)
            item.setData(Qt.UserRole, basemap)
            self.listBasemaps.addItem(item)

    def on_basemap_changed(self):
        # no longer need to show details
        pass

    def add_xyz_provider(self):
        dialog = ProviderInputDialog(self, provider_type="xyz")
        if dialog.exec_() == QDialog.Accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                QMessageBox.warning(
                    self,
                    self.tr("Warning"),
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                )
                return

            # Initialize XYZ provider data
            provider_data.update({
                "type": "xyz",
                "basemaps": []  # Initialize empty basemap list
            })

            # Add to data list
            self.providers_data.append(provider_data)
            
            # Update interface display
            self.update_providers_list()
            
            # Select new added provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if item and item.data(Qt.UserRole):
                    if item.data(Qt.UserRole)["data"]["name"] == provider_data["name"]:
                        self.listProviders.setCurrentItem(item)
                        break
            
            # Save config
            self.save_user_config()

    def remove_xyz_provider(self):
        """remove XYZ provider"""
        selected_items = self.listProviders.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select providers to remove."),
            )
            return

        # Get provider names to delete
        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove providers: "{}"?').format(names_str),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Collect indices to remove
            indices_to_remove = []
            for item in selected_items:
                provider_data = item.data(Qt.UserRole)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
            
            # Sort indices from large to small, so deleting will not affect other indices
            indices_to_remove.sort(reverse=True)
            
            # Delete provider
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

    def add_xyz_basemap(self):
        current_item = self.listProviders.currentItem()
        if not current_item:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select a provider first."),
            )
            return

        dialog = BasemapInputDialog(self, provider_type="xyz")
        if dialog.exec_() == QDialog.Accepted:
            provider_data = current_item.data(Qt.UserRole)
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
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def edit_xyz_basemap(self):
        """edit XYZ basemap"""
        current_provider = self.listProviders.currentItem()
        current_basemap = self.listBasemaps.currentItem()
        if not current_provider or not current_basemap:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select a basemap to edit."),
            )
            return

        provider_data = current_provider.data(Qt.UserRole)
        basemap = current_basemap.data(Qt.UserRole)

        dialog = BasemapInputDialog(self, basemap)
        if dialog.exec_() == QDialog.Accepted:
            # Get edited data
            new_data = dialog.get_data()
            
            # Update data
            provider_index = provider_data["index"]
            basemap_index = self.providers_data[provider_index]["basemaps"].index(basemap)
            self.providers_data[provider_index]["basemaps"][basemap_index] = new_data
            
            # Update interface display
            self.update_providers_list()
            
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_index
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            
            # Save config
            self.save_user_config()

    def remove_xyz_basemap(self):
        current_provider = self.listProviders.currentItem()
        selected_basemaps = self.listBasemaps.selectedItems()
        if not current_provider or not selected_basemaps:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select basemaps to remove."),
            )
            return

        names = [item.text() for item in selected_basemaps]
        names_str = '", "'.join(names)

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove basemaps: "{}"?').format(
                names_str
            ),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            provider_data = current_provider.data(Qt.UserRole)
            # Directly modify providers_data data
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(Qt.UserRole) for item in selected_basemaps]
            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # Re-select current provider
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def add_wms_provider(self):
        dialog = ProviderInputDialog(self, provider_type="wms")
        if dialog.exec_() == QDialog.Accepted:
            provider_data = dialog.get_data()
            if any(p["name"] == provider_data["name"] for p in self.providers_data):
                QMessageBox.warning(
                    self,
                    self.tr("Warning"),
                    self.tr("Provider '{}' already exists.").format(
                        provider_data["name"]
                    ),
                )
                return

            # Initialize WMS provider data
            provider_data.update({
                "type": "wms",
                "layers": []  # Initialize empty layer list
            })

            # Add to data list
            self.providers_data.append(provider_data)
            
            # Update interface display
            self.update_providers_list()
            
            # Select new added provider
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if item and item.data(Qt.UserRole):
                    if item.data(Qt.UserRole)["data"]["name"] == provider_data["name"]:
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
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select providers to remove."),
            )
            return

        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove providers: "{}"?').format(names_str),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # Collect indices to remove
            indices_to_remove = []
            for item in selected_items:
                provider_data = item.data(Qt.UserRole)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
            
            # Sort indices from large to small, so deleting will not affect other indices
            indices_to_remove.sort(reverse=True)
            
            # Delete provider
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # Update interface
            self.update_providers_list()
            self.save_user_config()

    def add_wms_layer(self):
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select a WMS provider first."),
            )
            return

        dialog = BasemapInputDialog(self, provider_type="wms")
        if dialog.exec_() == QDialog.Accepted:
            provider_data = current_item.data(Qt.UserRole)
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
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_data["index"]
                ):
                    self.listWmsProviders.setCurrentItem(item)
                    break
            self.save_user_config()

    def load_wms_layer(self):
        selected_items = self.listWmsLayers.selectedItems()
        if not selected_items:
            return

        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            return

        provider_data = current_provider.data(Qt.UserRole)
        url = provider_data["data"]["url"]

        for item in selected_items:
            layer_data = item.data(Qt.UserRole)
            # Build WMS parameters
            params = {
                "url": url,
                "layers": layer_data["layer_name"],
                "format": layer_data["format"][0],
                "crs": layer_data["crs"][0],
                "styles": layer_data["styles"][0] if layer_data["styles"] else "",
            }

            # If there are styles, add style parameters
            if layer_data.get("styles") and len(layer_data["styles"]) > 0:
                params["styles"] = layer_data["styles"][0]

            # Build URI using QgsDataSourceUri
            uri = QgsDataSourceUri()
            for key, value in params.items():
                uri.setParam(key, value)
            
            # Create layer
            layer = QgsRasterLayer(str(uri.encodedUri(), "utf-8"), layer_data["layer_title"], "wms")

            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
            else:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Failed to load WMS layer: {}").format(
                        layer_data["layer_title"]
                    ),
                )

    def refresh_wms_layers(self):
        """refresh current selected WMS provider's layer list"""
        current_provider = self.listWmsProviders.currentItem()
        if not current_provider:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select a WMS provider first."),
            )
            return

        provider_data = current_provider.data(Qt.UserRole)
        url = provider_data["data"]["url"]

        try:
            # Create progress dialog
            progress = QProgressDialog("Fetching WMS layers...", "Cancel", 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            QApplication.processEvents()

            # Connect to WMS service
            wms = WebMapService(url)

            # Get layer information
            layers = []
            for layer_name, layer in wms.contents.items():
                layer_info = {
                    "layer_name": layer_name,
                    "layer_title": layer.title,
                    "crs": [str(crs) for crs in layer.crsOptions],
                    "format": wms.getOperationByName("GetMap").formatOptions,
                    "styles": [
                        style.get("name", "") for style in layer.styles.values()
                    ],
                }
                layers.append(layer_info)

            # Sort by layer name
            layers.sort(key=lambda x: x["layer_name"].lower())

            # Update provider data
            index = provider_data["index"]
            provider = self.providers_data[index]

            # Check if it is a default provider
            try:
                with open(
                    Path(__file__).parent / "resources" / "default_basemaps.json",
                    "r",
                    encoding="utf-8",
                ) as f:
                    default_data = json.load(f)
                    default_providers = {
                        p["name"]: p for p in default_data.get("providers", [])
                    }
            except Exception:
                default_providers = {}

            if provider["name"] in default_providers:
                # If it is a default provider, create a new user-defined version
                new_provider = {
                    "name": f"{provider['name']} (Custom)",
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "url": url,
                    "layers": layers,
                }
                self.providers_data.append(new_provider)
            else:
                # If it is a user provider, directly update
                self.providers_data[index] = {
                    "name": provider["name"],
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "url": url,
                    "layers": layers,
                }

            # Update interface display
            self.update_providers_list()

            # Re-select current provider
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if item and item.data(Qt.UserRole):
                    item_data = item.data(Qt.UserRole)
                    if (
                        provider["name"] in default_providers
                        and item_data["data"]["name"] == f"{provider['name']} (Custom)"
                    ) or (
                        provider["name"] not in default_providers
                        and item_data["index"] == index
                    ):
                        self.listWmsProviders.setCurrentItem(item)
                        break

            # Save config to JSON file
            self.save_user_config()

            # Close progress dialog
            progress.close()

            # Show success message
            QMessageBox.information(
                self,
                self.tr("Success"),
                self.tr("Successfully refreshed WMS layers."),
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to fetch WMS layers: {}").format(str(e)),
            )
            progress.close()

    def show_xyz_provider_context_menu(self, position):
        menu = QMenu()
        edit_action = menu.addAction("Edit")
        action = menu.exec_(self.listProviders.mapToGlobal(position))
        
        if action == edit_action:
            self.edit_xyz_provider()

    def show_xyz_basemap_context_menu(self, position):
        menu = QMenu()
        edit_action = menu.addAction("Edit")
        action = menu.exec_(self.listBasemaps.mapToGlobal(position))
        
        if action == edit_action:
            self.edit_xyz_basemap()

    def show_wms_provider_context_menu(self, position):
        menu = QMenu()
        edit_action = menu.addAction("Edit")
        action = menu.exec_(self.listWmsProviders.mapToGlobal(position))
        
        if action == edit_action:
            current_item = self.listWmsProviders.currentItem()
            if not current_item:
                QMessageBox.warning(
                    self,
                    self.tr("Warning"),
                    self.tr("Please select a WMS provider to edit."),
                )
                return

            provider_data = current_item.data(Qt.UserRole)
            dialog = ProviderInputDialog(self, provider_data["data"], provider_type="wms")
            if dialog.exec_() == QDialog.Accepted:
                new_data = dialog.get_data()
                new_data["type"] = "wms"
                new_data["layers"] = provider_data["data"].get("layers", [])
                new_data["url"] = dialog.url_edit.text()
                
                # Update data
                self.providers_data[provider_data["index"]] = new_data
                self.update_providers_list()
                self.save_user_config()


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

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
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

        if self.provider_type == "wms":
            data["url"] = self.url_edit.text()

        return data


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

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_data(self):
        if self.provider_type == "wms":
            return {
                "name": self.name_edit.text(),
                "url": self.url_edit.text(),
                "layer_name": self.layer_name_edit.text(),
                "layer_title": self.layer_title_edit.text(),
                "crs": self.crs_edit.text(),
                "format": self.format_combo.currentText(),
            }
        else:
            return {"name": self.name_edit.text(), "url": self.url_edit.text()}

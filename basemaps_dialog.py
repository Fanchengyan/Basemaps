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

        # XYZ 相关连接
        self.btnAddProvider.clicked.connect(self.add_xyz_provider)
        self.btnRemoveProvider.clicked.connect(self.remove_xyz_provider)
        self.btnAddBasemap.clicked.connect(self.add_xyz_basemap)
        self.btnEditBasemap.clicked.connect(self.edit_xyz_basemap)
        self.btnRemoveBasemap.clicked.connect(self.remove_xyz_basemap)
        self.btnLoadBasemap.clicked.connect(self.load_xyz_basemap)
        self.listProviders.itemSelectionChanged.connect(self.on_provider_changed)

        # WMS 相关连接
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
                self.providers_data = data.get("providers", [])  # 只包含默认数据
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
                self.providers_data.extend(data.get("providers", []))  # 添加用户数据
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

                # 创建临时目录
                with tempfile.TemporaryDirectory() as temp_dir:
                    # 解压ZIP文件
                    with zipfile.ZipFile(file_path, "r") as zip_ref:
                        zip_ref.extractall(temp_dir)

                    # 查找JSON文件
                    json_files = list(Path(temp_dir).glob("*.json"))
                    if not json_files:
                        raise Exception("No JSON file found in ZIP")

                    # 加载JSON数据
                    with open(json_files[0], "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # 复制图标文件
                    icons_dir = Path(temp_dir) / "icons"
                    if icons_dir.exists():
                        target_icons_dir = Path(__file__).parent / "resources" / "icons"
                        target_icons_dir.mkdir(parents=True, exist_ok=True)
                        for icon_file in icons_dir.glob("*"):
                            shutil.copy2(icon_file, target_icons_dir)

                    # 更新数据
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
        """导出配置到 ZIP 文件"""
        # 检查是否有选中的项目
        selected_xyz_items = self.listProviders.selectedItems()
        selected_wms_items = self.listWmsProviders.selectedItems()
        
        if not selected_xyz_items and not selected_wms_items:
            # 询问用户是否要导出所有用户自定义的提供商
            reply = QMessageBox.question(
                self,
                self.tr("Export Configuration"),
                self.tr("No providers selected. Do you want to export all user-defined providers?"),
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        # 设置默认文件名
        default_filename = "providers.zip"
        if len(selected_xyz_items) + len(selected_wms_items) == 1:
            # 如果只选择了一个提供商，使用其名称作为默认文件名
            if selected_xyz_items:
                provider_name = selected_xyz_items[0].text()
            else:
                provider_name = selected_wms_items[0].text()
            default_filename = f"{provider_name}.zip"
        
        # 获取保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Configuration File"),
            default_filename,
            self.tr("ZIP files (*.zip)"),
        )
        if not file_path:
            return

        try:
            # 收集要导出的提供商和图标
            providers_to_export = []
            icon_files = set()
            
            if not selected_xyz_items and not selected_wms_items:
                # 找到分隔符的索引
                separator_index = next(
                    (i for i, p in enumerate(self.providers_data) if p.get("type") == "separator"),
                    -1
                )
                # 导出所有用户自定义的提供商
                providers = self.providers_data[separator_index + 1:] if separator_index >= 0 else []
                for provider in providers:
                    if provider.get("type") != "separator":
                        providers_to_export.append(provider)
                        if "icon" in provider:
                            icon_files.add(provider["icon"])
            else:
                # 处理选中的 XYZ 提供商
                for item in selected_xyz_items:
                    provider_data = item.data(Qt.UserRole)
                    if provider_data and provider_data.get("data"):
                        provider = provider_data["data"]
                        if provider.get("type") != "separator":
                            providers_to_export.append(provider)
                            if "icon" in provider:
                                icon_files.add(provider["icon"])
                
                # 处理选中的 WMS 提供商
                for item in selected_wms_items:
                    provider_data = item.data(Qt.UserRole)
                    if provider_data and provider_data.get("data"):
                        provider = provider_data["data"]
                        if provider.get("type") != "separator":
                            providers_to_export.append(provider)
                            if "icon" in provider:
                                icon_files.add(provider["icon"])

            # 创建临时目录并保存文件
            import tempfile
            import zipfile
            
            with tempfile.TemporaryDirectory() as temp_dir:
                # 保存 JSON 文件
                json_path = Path(temp_dir) / "providers.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"providers": providers_to_export},
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )

                # 创建 ZIP 文件
                with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    # 添加 JSON 文件
                    zipf.write(json_path, "providers.json")

                    # 添加图标文件
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

            # 标记为用户自定义提供商
            for provider in data.get("providers", []):
                provider["builtin"] = False

            # Merge with existing data
            existing_providers = {p["name"]: p for p in self.providers_data}
            for provider in data.get("providers", []):
                if provider["name"] in existing_providers:
                    # 如果内置提供商，创建一个新的用户自定义版本
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
            # 找到分隔符的索引
            separator_index = next(
                (i for i, p in enumerate(self.providers_data) if p.get("type") == "separator"),
                -1
            )
            
            # 只保存分隔符后的用户配置
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
        """更新提供商列表"""
        self.listProviders.clear()
        self.listWmsProviders.clear()
        
        # 设置列表的图标大小
        self.listProviders.setIconSize(QSize(15, 15))
        self.listWmsProviders.setIconSize(QSize(15, 15))

        def create_scaled_icon(icon_path):
            if icon_path.exists():
                original_icon = QIcon(str(icon_path))
                pixmap = original_icon.pixmap(QSize(15, 15))
                return QIcon(pixmap)
            return QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # 添加提供商到对应的列表
        for i, provider in enumerate(self.providers_data):
            # 如果是分隔符，添加不可选的分隔项
            if provider.get("type") == "separator":
                for list_widget in [self.listProviders, self.listWmsProviders]:
                    item = QListWidgetItem(provider["name"])
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                    list_widget.addItem(item)
                continue

            # 创建图标
            if "icon" in provider:
                icon_file = Path(__file__).parent / "resources" / provider["icon"]
                provider_icon = create_scaled_icon(icon_file)
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

            # 根据类型添加到不同的列表
            if provider.get("type") == "wms":
                item = QListWidgetItem(provider["name"])
                item.setIcon(provider_icon)
                item.setData(Qt.UserRole, {"index": i, "data": provider})
                self.listWmsProviders.addItem(item)
            else:  # xyz 类型
                # 确保 provider 有 basemaps 字段
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

        # 读取默认配置中的提供商数量
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

        # 检查是否选中了默认提供商
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

        # 获取要删除的提供商名称列表
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
            # 收集要删除的提供商索引
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
            # 直接修改 providers_data 中的数据
            self.providers_data[provider_data["index"]]["basemaps"].append(
                dialog.get_data()
            )
            self.update_providers_list()  # 刷新提供商列表
            # 重新选中当前提供商
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
            # 直接修改 providers_data 中的数据
            provider = self.providers_data[provider_data["index"]]
            basemap_index = provider["basemaps"].index(basemap)
            provider["basemaps"][basemap_index] = dialog.get_data()
            self.update_providers_list()
            # 重新选中当前提供商
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

        # 获取要删除的底图名称列表
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
            # 直接修改 providers_data 中的数据
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(Qt.UserRole) for item in selected_basemaps]
            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # 重新选中当前提供商
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

        provider_data = current_provider.data(Qt.UserRole)
        provider_type = provider_data["data"]["type"]

        for item in selected_items:
            basemap = item.data(Qt.UserRole)
            self.load_xyz_basemap(basemap)

    def load_xyz_basemap(self):
        selected_items = self.listBasemaps.selectedItems()
        if not selected_items:
            return

        for item in selected_items:
            basemap = item.data(Qt.UserRole)
            if not basemap:  # 添加检查
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
        """更新 XYZ 底图列表"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            self.listBasemaps.clear()
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data or "data" not in provider_data:  # 添加检查
            return

        # 设置底图列表的图标大小
        self.listBasemaps.setIconSize(QSize(15, 15))

        # 获取提供商的图标
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # 更新底图列表
        self.listBasemaps.clear()
        for basemap in provider_data["data"].get("basemaps", []):
            if (
                isinstance(basemap, dict) and "name" in basemap and "url" in basemap
            ):  # 添加检查
                item = QListWidgetItem(basemap["name"])
                item.setIcon(provider_icon)
                item.setData(Qt.UserRole, basemap)
                self.listBasemaps.addItem(item)

    def on_wms_provider_changed(self):
        """当 WMS 提供商选择改变时更新图层列表"""
        current_item = self.listWmsProviders.currentItem()
        if not current_item:
            self.listWmsLayers.clear()
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data:
            return

        # 设置图层列表的图标大小
        self.listWmsLayers.setIconSize(QSize(15, 15))

        # 获取提供商的图标
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # 更新图层列表
        self.listWmsLayers.clear()
        for layer in provider_data["data"].get("layers", []):
            # 使用 layer_title 作为显示名称，如果没有则使用 layer_name
            display_name = layer.get(
                "layer_title", layer.get("layer_name", "Unknown Layer")
            )
            item = QListWidgetItem(display_name)
            item.setIcon(provider_icon)
            item.setData(Qt.UserRole, layer)
            self.listWmsLayers.addItem(item)

    def update_basemaps_list(self):
        """更新 XYZ 底图列表"""
        current_item = self.listProviders.currentItem()
        if not current_item:
            return

        provider_data = current_item.data(Qt.UserRole)
        if not provider_data:
            return

        # 设置底图列表的图标大小
        self.listBasemaps.setIconSize(QSize(15, 15))

        # 获取提供商的图标
        provider = provider_data["data"]
        if "icon" in provider:
            icon_file = Path(__file__).parent / "resources" / provider["icon"]
            if icon_file.exists():
                provider_icon = QIcon(str(icon_file))
            else:
                provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))
        else:
            provider_icon = QIcon(str(Path(__file__).parent / "ui/icon.svg"))

        # 更新底图列表
        self.listBasemaps.clear()
        for basemap in provider_data["data"]["basemaps"]:
            item = QListWidgetItem(basemap["name"])
            item.setIcon(provider_icon)
            item.setData(Qt.UserRole, basemap)
            self.listBasemaps.addItem(item)

    def on_basemap_changed(self):
        # 不再需要显示详情
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

            # 初始化 XYZ 提供商数据
            provider_data.update({
                "type": "xyz",
                "basemaps": []  # 初始化空的底图列表
            })

            # 添加到数据列表
            self.providers_data.append(provider_data)
            
            # 更新界面显示
            self.update_providers_list()
            
            # 选中新添加的提供商
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if item and item.data(Qt.UserRole):
                    if item.data(Qt.UserRole)["data"]["name"] == provider_data["name"]:
                        self.listProviders.setCurrentItem(item)
                        break
            
            # 保存配置
            self.save_user_config()

    def remove_xyz_provider(self):
        """删除 XYZ 提供商"""
        selected_items = self.listProviders.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Please select providers to remove."),
            )
            return

        # 获取要删除的提供商名称列表
        provider_names = [item.text() for item in selected_items]
        names_str = '", "'.join(provider_names)

        reply = QMessageBox.question(
            self,
            self.tr("Confirm Deletion"),
            self.tr('Are you sure you want to remove providers: "{}"?').format(names_str),
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # 收集要删除的索引
            indices_to_remove = []
            for item in selected_items:
                provider_data = item.data(Qt.UserRole)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
            
            # 按照索引从大到小排序，这样删除时不会影响其他索引
            indices_to_remove.sort(reverse=True)
            
            # 删除提供商
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # 更新界面
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
            # 直接修改 providers_data 中的数据
            self.providers_data[provider_data["index"]]["basemaps"].append(
                dialog.get_data()
            )
            self.update_providers_list()
            # 重新选中当前提供商
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
        """编辑 XYZ 底图"""
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
            # 获取编辑后的数据
            new_data = dialog.get_data()
            
            # 更新数据
            provider_index = provider_data["index"]
            basemap_index = self.providers_data[provider_index]["basemaps"].index(basemap)
            self.providers_data[provider_index]["basemaps"][basemap_index] = new_data
            
            # 更新界面显示
            self.update_providers_list()
            
            # 重新选中当前提供商
            for i in range(self.listProviders.count()):
                item = self.listProviders.item(i)
                if (
                    item
                    and item.data(Qt.UserRole)
                    and item.data(Qt.UserRole)["index"] == provider_index
                ):
                    self.listProviders.setCurrentItem(item)
                    break
            
            # 保存配置
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
            # 直接修改 providers_data 中的数据
            provider = self.providers_data[provider_data["index"]]
            basemaps_to_remove = [item.data(Qt.UserRole) for item in selected_basemaps]
            provider["basemaps"] = [
                b for b in provider["basemaps"] if b not in basemaps_to_remove
            ]
            self.update_providers_list()
            # 重新选中当前提供商
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

            # 初始化 WMS 提供商数据
            provider_data.update({
                "type": "wms",
                "layers": []  # 初始化空的图层列表
            })

            # 添加到数据列表
            self.providers_data.append(provider_data)
            
            # 更新界面显示
            self.update_providers_list()
            
            # 选中新添加的提供商
            for i in range(self.listWmsProviders.count()):
                item = self.listWmsProviders.item(i)
                if item and item.data(Qt.UserRole):
                    if item.data(Qt.UserRole)["data"]["name"] == provider_data["name"]:
                        self.listWmsProviders.setCurrentItem(item)
                        break
            
            # 保存配置
            self.save_user_config()

            # 自动触发刷新
            self.refresh_wms_layers()

    def remove_wms_provider(self):
        """删除 WMS 提供商"""
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
            # 收集要删除的索引
            indices_to_remove = []
            for item in selected_items:
                provider_data = item.data(Qt.UserRole)
                if provider_data:
                    indices_to_remove.append(provider_data["index"])
            
            # 按照索引从大到小排序
            indices_to_remove.sort(reverse=True)
            
            # 删除提供商
            for index in indices_to_remove:
                self.providers_data.pop(index)

            # 更新界面
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
            # 直接修改 providers_data 中的数据
            self.providers_data[provider_data["index"]]["layers"].append(
                dialog.get_data()
            )
            self.update_providers_list()
            # 重新选中当前提供商
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
            # 构建 WMS 参数
            params = {
                "url": url,
                "layers": layer_data["layer_name"],
                "format": layer_data["format"][0],
                "crs": layer_data["crs"][0],
                "styles": layer_data["styles"][0] if layer_data["styles"] else "",
            }

            # 如果有样式，添加样式参数
            if layer_data.get("styles") and len(layer_data["styles"]) > 0:
                params["styles"] = layer_data["styles"][0]

            # 使用 QgsDataSourceUri 构建 URI
            uri = QgsDataSourceUri()
            for key, value in params.items():
                uri.setParam(key, value)
            
            # 创建图层
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
        """刷新当前选中 WMS 提供商的图层列表"""
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
            # 创建进度对话框
            progress = QProgressDialog("Fetching WMS layers...", "Cancel", 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            QApplication.processEvents()

            # 连接 WMS 服务
            wms = WebMapService(url)

            # 获取图层信息
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

            # 按图层名称排序
            layers.sort(key=lambda x: x["layer_name"].lower())

            # 更新提供商数据
            index = provider_data["index"]
            provider = self.providers_data[index]

            # 检查是否是默认提供商
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
                # 如果是默认提供商，创建一个新的用户自定义版本
                new_provider = {
                    "name": f"{provider['name']} (Custom)",
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "url": url,
                    "layers": layers,
                }
                self.providers_data.append(new_provider)
            else:
                # 如果是用户提供商，直接更新
                self.providers_data[index] = {
                    "name": provider["name"],
                    "icon": provider.get("icon", "ui/icon.svg"),
                    "type": "wms",
                    "url": url,
                    "layers": layers,
                }

            # 更新界面显示
            self.update_providers_list()

            # 重新选中当前提供商
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

            # 保存配置到 JSON 文件
            self.save_user_config()

            # 关闭进度对话框
            progress.close()

            # 显示成功消息
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
                
                # 更新数据
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

        # URL input (只在 WMS 类型时显示)
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

        # Layer settings (只在 WMS 类型时显示)
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

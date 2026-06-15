# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

from pathlib import Path

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QTranslator
from qgis.PyQt.QtWidgets import QAction

from .basemaps_dialog import BasemapsDialog
from .ui import IconBasemaps


class BasemapsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = Path(__file__).parent
        self.actions = []

        # Initialize translator
        locale = QSettings().value("locale/userLocale")[0:2]
        locale_path = self.plugin_dir / "i18n" / f"Basemaps_{locale}.qm"
        self.translator = None
        if locale_path.exists():
            self.translator = QTranslator()
            if self.translator.load(str(locale_path)):
                QCoreApplication.installTranslator(self.translator)

        self.menu = self.tr("Basemap Management")
        self.dialog = None

    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate("BasemapsPlugin", message)

    def initGui(self):
        action = QAction(
            IconBasemaps, self.tr("Load Basemaps"), self.iface.mainWindow()
        )
        action.triggered.connect(self.run)

        self.iface.addToolBarIcon(action)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

        # Register the Browser panel integration. Guarded so reloading the
        # plugin (e.g. via Plugin Reloader) never produces duplicate nodes.
        self._register_browser_provider()

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)

        self._unregister_browser_provider()

        if self.translator:
            QCoreApplication.removeTranslator(self.translator)

    # ------------------------------------------------------------------
    # Browser panel integration
    # ------------------------------------------------------------------

    def _register_browser_provider(self) -> None:
        """Register the Basemaps Browser data item provider.

        Idempotent: if a provider with the same name is already registered
        (e.g. after a plugin reload), it is removed first so QGIS never
        shows two ``Basemaps`` nodes.
        """
        try:
            from qgis.core import QgsApplication

            from .browser_provider import (
                PROVIDER_NAME,
                BasemapsDataItemProvider,
            )

            registry = QgsApplication.dataItemProviderRegistry()

            # Remove any previously-registered instance to avoid duplicates.
            existing = None
            for provider in registry.providers():
                if provider.name() == PROVIDER_NAME:
                    existing = provider
                    break
            if existing is not None:
                registry.removeProvider(existing)

            self._browser_provider = BasemapsDataItemProvider(icon=IconBasemaps)
            registry.addProvider(self._browser_provider)

            # Register the token-missing callback so the Browser layer
            # loader can open the Edit Provider dialog when needed.
            from .layer_loader import set_token_missing_callback

            def _on_token_missing(name, provider_type):
                if not self.dialog:
                    self.dialog = BasemapsDialog(self.iface)
                self.dialog.show()
                self.dialog.raise_()
                self.dialog.activateWindow()
                self.dialog.edit_provider_by_name(name, provider_type)

            set_token_missing_callback(_on_token_missing)
        except Exception as e:
            # Browser integration is a convenience feature; never let it
            # break plugin loading.
            from .messageTool import Logger

            Logger.warning(
                f"Failed to register Basemaps Browser provider: {e}",
                notify_user=False,
            )

    def _unregister_browser_provider(self) -> None:
        """Remove the Browser data item provider if it is still registered."""
        try:
            from qgis.core import QgsApplication

            from .browser_provider import PROVIDER_NAME

            registry = QgsApplication.dataItemProviderRegistry()
            for provider in list(registry.providers()):
                if provider.name() == PROVIDER_NAME:
                    registry.removeProvider(provider)
        except Exception:
            pass
        finally:
            self._browser_provider = None

        try:
            from .layer_loader import set_token_missing_callback

            set_token_missing_callback(None)
        except Exception:
            pass

    def run(self):
        if not self.dialog:
            self.dialog = BasemapsDialog(self.iface)
        self.dialog.show()

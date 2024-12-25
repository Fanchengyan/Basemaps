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

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)

        if self.translator:
            QCoreApplication.removeTranslator(self.translator)

    def run(self):
        if not self.dialog:
            self.dialog = BasemapsDialog(self.iface)
        self.dialog.show()

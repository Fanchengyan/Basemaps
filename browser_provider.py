# Copyright (C) 2024  Chengyan (Fancy) Fan

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""QGIS Browser ``QgsDataItemProvider`` for the Basemaps plugin.

Registered in :func:`basemaps.BasemapsPlugin.initGui` and removed in
:func:`basemaps.BasemapsPlugin.unload`. The provider creates the
top-level :class:`browser_items.BasemapsRootItem`, from which the rest of
the tree is built lazily.
"""

from __future__ import annotations

from qgis.core import QgsDataItem, QgsDataItemProvider, QgsDataProvider

from .browser_items import BasemapsRootItem

# Unique name used to (de)register the provider. Keeping it as a module
# constant makes the duplicate-registration guard in basemaps.py explicit.
PROVIDER_NAME = "BasemapsBrowserProvider"


class BasemapsDataItemProvider(QgsDataItemProvider):
    """Creates the root ``Basemaps`` node in the Browser panel."""

    def __init__(self, icon=None) -> None:
        super().__init__()
        # Hold the icon so it is not garbage-collected before the root item
        # is built in createDataItem().
        self._icon = icon

    def name(self):  # noqa: D401 - QGIS virtual override
        return PROVIDER_NAME

    def capabilities(self):  # noqa: D401 - QGIS virtual override
        # Net capability keeps the item visible without implying file access.
        return QgsDataProvider.Net

    def createDataItem(self, path, parentItem):  # noqa: D401 - QGIS virtual override
        # The Browser calls createDataItem for every provider for every path.
        # We only want to add a single top-level node, so we respond only when
        # there is no parent (the root of the browser tree).
        if parentItem is not None:
            return None
        from qgis.PyQt import sip

        root = BasemapsRootItem(icon=self._icon)
        sip.transferto(root, None)
        return root

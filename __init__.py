def classFactory(iface):
    from .basemaps import BasemapsPlugin

    return BasemapsPlugin(iface)

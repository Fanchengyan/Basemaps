# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Basemaps is a QGIS 3.0+ plugin that provides easy access to online basemap services (XYZ tiles, WMS, WMTS). Compatible with QGIS 3.0-4.99 and both PyQt5/PyQt6.

## Architecture

```
Basemaps/
├── __init__.py           # Plugin entry point (classFactory)
├── basemaps.py           # Main plugin class (BasemapsPlugin)
├── basemaps_dialog.py    # Dialog classes (BasemapsDialog, ProviderInputDialog, BasemapInputDialog)
├── ui/
│   ├── __init__.py       # UI loading, icon setup
│   └── basemaps_dialog_base.ui  # Qt Designer UI definition
├── resources/
│   ├── default_basemaps.json    # Built-in provider definitions
│   ├── user_basemaps.json       # User-customized providers
│   └── icons/                   # Provider icons
└── i18n/                 # Translations (zh, ja, fr, de, ru, ko)
```

### Key Components

- **`__init__.py`**: Implements `classFactory(iface)` - standard QGIS plugin entry point
- **`basemaps.py`**: Plugin lifecycle (initGui, unload), toolbar/menu integration, translation setup
- **`basemaps_dialog.py`**: Main UI logic - two tabs for XYZ and WMS services, provider/basemap CRUD, import/export (ZIP with JSON + icons)
- **`ui/__init__.py`**: Dynamically loads `.ui` file using `uic.loadUiType()`

### Data Flow

1. JSON configs (`default_basemaps.json`, `user_basemaps.json`) define providers and basemaps
2. Dialog loads configs and populates QListWidgets with icons
3. User selections create QgsRasterLayer with 'xyz' or 'wms' provider
4. User modifications save back to `user_basemaps.json`

## Development

### No Build System
This is a pure Python QGIS plugin - no compilation needed. Install by copying to QGIS plugins directory.

### Testing
No automated tests. Manual testing in QGIS required.

### Translation Workflow
Uses Qt Linguist system:
```bash
# Update .ts files from source
pylupdate5 i18n/Basemaps.pro

# Edit translations in Qt Linguist, then compile
lrelease i18n/Basemaps.pro
```

## Qt5/Qt6 Compatibility

The codebase handles both Qt versions. Key pattern in `basemaps_dialog.py`:
```python
from qgis.PyQt.QtCore import QT_VERSION_STR
if int(QT_VERSION_STR.split(".")[0]) >= 6:
    # Qt6 enum paths
else:
    # Qt5 enum paths
```

## External Dependencies

- **owslib**: WMS capabilities parsing (`owslib.wms.WebMapService`)
- **requests**: HTTP requests for WMS services
- Standard QGIS/Qt APIs

## JSON Config Structure

```json
{
  "providers": [
    {
      "name": "Provider Name",
      "icon": "path/to/icon",
      "type": "xyz|wms|separator",
      "basemaps": [{"name": "...", "url": "..."}],  // for xyz
      "url": "WMS endpoint",                         // for wms
      "layers": [{"name": "...", "url": "...", "layer_name": "...", "crs": "...", "format": "..."}]
    }
  ]
}
```

## Contributing

- GitHub: https://github.com/Fanchengyan/Basemaps
- Issues: https://github.com/Fanchengyan/Basemaps/issues

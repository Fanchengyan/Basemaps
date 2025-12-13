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
├── config_loader.py      # Configuration loader (YAML/JSON support, directory migration)
├── ui/
│   ├── __init__.py       # UI loading, icon setup
│   └── basemaps_dialog_base.ui  # Qt Designer UI definition
├── resources/
│   ├── providers/                # Provider configuration files (NEW STRUCTURE)
│   │   ├── default/              # Built-in default providers
│   │   │   ├── xyz_Esri.yaml
│   │   │   ├── wms_EOX_Sentinel-2_Cloudless.yaml
│   │   │   └── ...
│   │   └── user/                 # User-customized providers
│   │       ├── xyz_CustomProvider.yaml
│   │       └── ...
│   ├── icons/                    # Provider icons
└── i18n/                 # Translations (zh, ja, fr, de, ru, ko)
```

### Key Components

- **`__init__.py`**: Implements `classFactory(iface)` - standard QGIS plugin entry point
- **`basemaps.py`**: Plugin lifecycle (initGui, unload), toolbar/menu integration, translation setup, PyYAML availability check
- **`basemaps_dialog.py`**: Main UI logic - two tabs for XYZ and WMS services, provider/basemap CRUD, import/export (ZIP with YAML/JSON + icons), automatic migration on startup
- **`config_loader.py`**: Unified configuration loader supporting both YAML and JSON formats with automatic detection, handles new directory structure, automatic migration from old structure
- **`ui/__init__.py`**: Dynamically loads `.ui` file using `uic.loadUiType()`

### Data Flow

1. On startup, automatically migrates old structure files to new structure if needed
2. Loads providers from `resources/providers/{default|user}/*.yaml`
3. Falls back to legacy locations and formats for backward compatibility
4. Dialog loads configs and populates QListWidgets with icons, displays "Default" and "User" separators
5. User selections create QgsRasterLayer with 'xyz' or 'wms' provider
6. User modifications save to individual YAML files in `resources/providers/user/`
7. Provider deletions remove the corresponding YAML file from disk

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

- **PyYAML**: YAML configuration file parsing (optional - falls back to JSON if unavailable)
- **owslib**: WMS capabilities parsing (`owslib.wms.WebMapService`)
- **requests**: HTTP requests for WMS services
- Standard QGIS/Qt APIs

## Configuration File Formats

### YAML Config Structure (Preferred)

**Type-separated files (default configs):**
```yaml
xyz:
  ProviderName:
    icon: icons/provider.svg
    basemaps:
      - name: Basemap Name
        url: https://example.com/tile/{z}/{x}/{y}.png

wms:
  ProviderName:
    icon: icons/provider.png
    url: https://example.com/WMTSCapabilities.xml
    service_type: wmts
    layers:
      - layer_name: layer_id
        layer_title: Layer Display Name
        crs: [EPSG:4326, EPSG:3857]
        format: [image/jpeg]
        styles: [default]
        service_type: wmts
```

**Mixed file (user configs):**
```yaml
xyz:
  Provider1:
    icon: icons/p1.svg
    basemaps:
      - name: Map1
        url: https://example.com/{z}/{x}/{y}.png

wms:
  Provider2:
    icon: icons/p2.png
    url: https://example.com/wms
    layers:
      - layer_name: layer1
        layer_title: Layer 1
        crs: [EPSG:4326]
        format: [image/png]
```

### Legacy JSON Config Structure

Still supported for backward compatibility:

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

## Configuration Loading Priority

1. **YAML files** (if PyYAML available):
   - `default_xyz.yaml` + `default_wms.yaml` for defaults
   - `user_basemaps.yaml` for user configs
2. **JSON files** (fallback):
   - `default_basemaps.json` for defaults
   - `user_basemaps.json` for user configs

## Migration Notes

- Original JSON files backed up as `.json.bak`
- Use `test_yaml_migration.py` to verify YAML configs
- See `YAML_FORMAT_REFERENCE.md` for detailed YAML syntax guide
- See `YAML_MIGRATION_REPORT.md` for complete migration details

## Contributing

- GitHub: https://github.com/Fanchengyan/Basemaps
- Issues: https://github.com/Fanchengyan/Basemaps/issues

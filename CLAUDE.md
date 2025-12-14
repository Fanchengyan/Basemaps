# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Basemaps is a QGIS 3.0+ plugin that provides easy access to online basemap services (XYZ tiles, WMS, WMTS). Compatible with QGIS 3.0-4.99 and both PyQt5/PyQt6.

## Architecture

```
Basemaps/
├── __init__.py              # Plugin entry point (classFactory)
├── basemaps.py              # Main plugin class (BasemapsPlugin)
├── basemaps_dialog.py       # Dialog classes (BasemapsDialog, ProviderInputDialog, BasemapInputDialog)
├── config_loader.py         # YAML configuration loader
├── wms_fetch_task.py        # Background WMS/WMTS fetching (QgsTask)
├── wmts_parser.py           # ElementTree-based WMTS XML parser
├── messageTool.py           # Logger, MessageBar, MessageBox wrappers
├── ui/
│   ├── __init__.py          # UI loading, icon setup
│   └── basemaps_dialog_base.ui  # Qt Designer UI definition
├── resources/
│   ├── providers/           # Provider configuration files
│   │   ├── default/         # Built-in default providers (YAML)
│   │   │   ├── xyz_01_Esri.yaml
│   │   │   ├── wms_01_EOX_Sentinel-2_Cloudless.yaml
│   │   │   └── ...
│   │   └── user/            # User-customized providers (YAML)
│   │       └── ...
│   ├── icons/               # Provider icons (SVG/PNG)
└── i18n/                    # Translations (zh, ja, fr, de, ru, ko)
```

### Key Components

- **[`__init__.py`](/__init__.py)**: Implements `classFactory(iface)` - standard QGIS plugin entry point
- **[`basemaps.py`](basemaps.py)**: Plugin lifecycle (initGui, unload), toolbar/menu integration, translation setup
- **[`basemaps_dialog.py`](basemaps_dialog.py)**: Main UI logic - two tabs for XYZ and WMS services, provider/basemap CRUD, import/export (ZIP with YAML + icons)
- **[`config_loader.py`](config_loader.py)**: YAML configuration loader, converts YAML type-based structure to providers list
- **[`wms_fetch_task.py`](wms_fetch_task.py)**: Asynchronous WMS/WMTS service capabilities fetching using `QgsTask`, prevents UI blocking during network requests
- **[`wmts_parser.py`](wmts_parser.py)**: ElementTree-based WMTS capabilities parser, used as fallback when OWSLib fails on non-standard XML
- **[`messageTool.py`](messageTool.py)**: Unified logging and messaging utilities - `Logger` (QgsMessageLog), `MessageBar`, `MessageBox` with Qt5/Qt6 compatibility
- **[`ui/__init__.py`](ui/__init__.py)**: Dynamically loads `.ui` file using `uic.loadUiType()`

### Data Flow

1. Plugin starts via `classFactory()` → `BasemapsPlugin.__init__()` → `initGui()`
2. User clicks "Load Basemaps" → `BasemapsDialog` created
3. Dialog loads YAML configs from `resources/providers/{default|user}/*.yaml` via `config_loader`
4. XYZ/WMS providers populate QListWidgets with icons and separators ("Default"/"User")
5. **WMS/WMTS fetch**: User adds WMS/WMTS provider → `WMSFetchTask` fetches capabilities asynchronously:
   - Primary: `owslib.wms.WebMapService` or `owslib.wmts.WebMapTileService`
   - Fallback: `wmts_parser.parse_wmts_capabilities()` for non-standard XML
6. User selections create `QgsRasterLayer` with 'xyz' or 'wms' provider
7. User modifications save to individual YAML files in `resources/providers/user/`
8. Provider deletions remove the corresponding YAML file

## Development

### Installation

Pure Python QGIS plugin - no build system or compilation needed. Install by copying to QGIS plugins directory:

- **Linux/Mac**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/Basemaps/`
- **Windows**: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\Basemaps\`

### Testing

No automated tests. Manual testing in QGIS required.

### Plugin Reload During Development

Use "Plugin Reloader" plugin in QGIS for faster development iterations without restarting QGIS.

## Qt5/Qt6 Compatibility

The codebase handles both Qt versions using runtime detection. Key pattern:

```python
from qgis.PyQt.QtCore import QT_VERSION_STR
if int(QT_VERSION_STR.split(".")[0]) >= 6:
    # Qt6: QMessageBox.StandardButton.Ok
    # Qt6: QListWidget.SelectionMode.SingleSelection
else:
    # Qt5: QMessageBox.Ok
    # Qt5: QListWidget.SingleSelection
```

See [`basemaps_dialog.py`](basemaps_dialog.py) and [`messageTool.py`](messageTool.py) for examples.

## External Dependencies

- **PyYAML**: YAML configuration parsing (required, no JSON fallback)
- **owslib**: WMS/WMTS capabilities parsing (`owslib.wms.WebMapService`, `owslib.wmts.WebMapTileService`)
- **requests**: HTTP requests for WMS/WMTS services
- Standard QGIS/Qt APIs (`qgis.core`, `qgis.PyQt`)

## Configuration File Format

**YAML structure** (all configs use this format):

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

**File naming convention**:
- Default configs: `{xyz|wms}_##_ProviderName.yaml` (e.g., `xyz_01_Esri.yaml`)
- User configs: `{xyz|wms}_ProviderName.yaml`

## Asynchronous WMS/WMTS Fetching

When users add WMS/WMTS providers, capabilities are fetched asynchronously:

1. **[`wms_fetch_task.py:91`](wms_fetch_task.py#L91)**: `WMSFetchTask` extends `QgsTask` for background processing
2. Runs in worker thread via `QgsApplication.taskManager().addTask()`
3. Auto-detects service type (WMS vs WMTS) from URL
4. **Dual parsing strategy**:
   - Try OWSLib first (standard-compliant services)
   - Fallback to [`wmts_parser.py`](wmts_parser.py) ElementTree parser (handles non-standard XML, namespace issues)
5. Emits `finished` signal with `FetchResult` dataclass on completion
6. UI remains responsive during network operations

## Logging and Messaging

Use [`messageTool.py`](messageTool.py) utilities instead of raw QGIS APIs:

- **`Logger.info(msg)`**: Log to QGIS message panel
- **`Logger.warning(msg, notify_user=True)`**: Log + show in message bar
- **`Logger.critical(msg)`**: Log critical errors
- **`MessageBar.show(title, text, level)`**: Temporary message bar notification
- **`MessageBox.yes_no(text)`**: Modal dialogs with Qt5/Qt6 compatibility

## Translation Workflow

Qt Linguist system for i18n. Use the appropriate commands based on your Qt version:

```bash
# Update .ts files from source code
# For PyQt5 (QGIS 3.0-3.27):
pylupdate5 i18n/Basemaps.pro
# For PyQt6 (QGIS 3.28+):
pylupdate6 i18n/Basemaps.pro

# Edit translations in Qt Linguist GUI
linguist i18n/basemaps_zh.ts  # or other language files

# Compile .ts → .qm
# For PyQt5:
lrelease i18n/Basemaps.pro
# For PyQt6:
lrelease-qt6 i18n/Basemaps.pro
```

**Tool installation**:

- **macOS**: Install via Homebrew:

  ```bash
  brew install qt@5              # For Qt5/PyQt5
  export PATH="/usr/local/opt/qt@5/bin:$PATH"
  # Or
  brew install qt                # For Qt6/PyQt6
  ```

- **Linux**: Install development packages:

  ```bash
  sudo apt-get install qttools5-dev-tools  # For Qt5
  # Or
  sudo apt-get install qt6-tools-dev       # For Qt6
  ```

- **Windows**: Install Qt from [qt.io](https://www.qt.io/download) and add the `bin` directory to your PATH

**Translation files**: Located in `i18n/` directory

- Source files (editable): `*.ts` files
- Compiled files (generated): `*.qm` files
- Supported languages: zh (Chinese), ja (Japanese), fr (French), de (German), ru (Russian), ko (Korean)

**Note**: The `i18n/Basemaps.pro` file lists all source files with translatable strings. When adding new Python files with user-facing messages, add them to the `SOURCES` list in this file.

## Contributing

- GitHub: https://github.com/Fanchengyan/Basemaps
- Issues: https://github.com/Fanchengyan/Basemaps/issues

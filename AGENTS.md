# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Basemaps is a QGIS 3.0+ plugin that provides easy access to online basemap services (XYZ tiles, WMS, WMTS). Compatible with QGIS 3.0-4.99 and both PyQt5/PyQt6.

## Architecture

```
Basemaps/
├── __init__.py              # Plugin entry point (classFactory)
├── basemaps.py              # Main plugin class (BasemapsPlugin)
├── basemaps_dialog.py       # Dialog classes + VectorTileLoadTask (QgsTask)
├── config_loader.py         # YAML configuration loader + tag overrides
├── preview_manager.py       # Preview thumbnail fetching, caching, rendering
├── wms_fetch_task.py        # Background WMS/WMTS capabilities fetching (QgsTask)
├── wmts_parser.py           # ElementTree-based WMTS XML parser (fallback)
├── messageTool.py           # Logger, MessageBar, MessageBox wrappers
├── metadata.txt             # QGIS plugin metadata (version, author, dependencies)
├── ui/
│   ├── __init__.py          # UI loading, icon setup
│   ├── basemaps_dialog_base.ui  # Qt Designer UI definition (880×531px)
│   └── basemap_delegate.py  # Custom QStyledItemDelegate for gallery card view
├── resources/
│   ├── providers/           # Provider configuration files (one YAML per provider)
│   │   ├── default/         # Built-in default providers (read-only, 12+ files)
│   │   └── user/            # User-customized providers (editable)
│   ├── previews/            # Cached preview thumbnails (PNG)
│   │   ├── default/{xyz,wms}/
│   │   └── user/{xyz,wms}/
│   ├── icons/               # Provider icons (SVG/PNG)
│   └── tag_overrides.yaml   # Persisted user tag edits (separate file mode)
└── i18n/                    # Translations (11 languages: zh, ja, ko, fr, de, ru, ar, es, pt, hi, bn)
```

### Key Components

- **[`__init__.py`](/__init__.py)**: Implements `classFactory(iface)` — standard QGIS plugin entry point
- **[`basemaps.py`](basemaps.py)**: Plugin lifecycle (`initGui`, `unload`), toolbar/menu integration, translation setup from QSettings locale
- **[`basemaps_dialog.py`](basemaps_dialog.py)**: Main UI logic (3,590 lines). Contains 6 classes:
  - `BasemapsDialog` — Two-tab dialog (XYZ/Vector Tiles + WMS/WMTS), provider/basemap CRUD, chunked UI rendering, tag filtering, search, gallery grid view, import/export (ZIP), context menus, bidirectional view mode sync
  - `ProviderInputDialog` — Modal form for Add/Edit Provider with fields that vary by provider type
  - `BasemapInputDialog` — Modal form for Add/Edit Basemap/Layer with tile type (raster/vector), source URL, style URL, layer settings, and tag selector
  - `TagEditDialog` — Tag editing with save-mode preference (separate overrides file vs direct provider file)
  - `TokenAuthWidget` — Checkable `QGroupBox` for API token auth with well-known provider presets (MapTiler, Mapbox, Thunderforest, etc.)
  - `VectorTileLoadTask` — `QgsTask` that downloads vector tile style JSON in background
- **[`config_loader.py`](config_loader.py)**: YAML config loading/saving, converts YAML type-based structure to providers list, tag overrides persistence, provider file rename/delete management
- **[`preview_manager.py`](preview_manager.py)**: Preview thumbnail system (1,783 lines). Multi-zoom tile fetching with retry, composite tile merging, vector tile off-screen rendering, WMTS capabilities caching, auth query param propagation, blank preview detection, placeholder generation
- **[`wms_fetch_task.py`](wms_fetch_task.py)**: Asynchronous WMS/WMTS capabilities fetching using `QgsTask`, auto-detects service type, dual parsing (OWSLib + `wmts_parser`), namespace fixing for ArcGIS
- **[`wmts_parser.py`](wmts_parser.py)**: ElementTree-based WMTS XML parser, namespace-aware, used as fallback when OWSLib fails on non-standard/ArcGIS XML
- **[`messageTool.py`](messageTool.py)**: `Logger` (QgsMessageLog), `MessageBar`, `MessageBox` with Qt5/Qt6 compatibility
- **[`ui/__init__.py`](ui/__init__.py)**: Loads `.ui` via `uic.loadUiType()`, Qt6 Designer enum-scope fix
- **[`ui/basemap_delegate.py`](ui/basemap_delegate.py)**: Custom `QStyledItemDelegate` for gallery card rendering with colored tag badges, protocol badges, hover expansion, rounded corners, shadows

### Data Flow

1. Plugin starts via `classFactory()` → `BasemapsPlugin.__init__()` → `initGui()`
2. User clicks "Load Basemaps" → `BasemapsDialog` created
3. Dialog loads YAML configs from `resources/providers/{default|user}/*.yaml` via `config_loader`
4. Applies tag overrides from `resources/tag_overrides.yaml` if present
5. XYZ/WMS providers populate QListWidgets with icons and separators ("Default"/"User") using chunked rendering (15 items per `QTimer` tick) to keep UI responsive for large providers (e.g., Wayback with 170+ layers)
6. **Preview thumbnails**: `PreviewManager` fetches tiles asynchronously with multi-zoom escalation and retry, caches PNGs to `resources/previews/`, emits signals to update gallery cards
7. **Tag filtering**: Combo box at top filters basemaps by tag category; providers without matching items are hidden
8. **WMS/WMTS fetch**: User adds WMS/WMTS provider → `WMSFetchTask` fetches capabilities asynchronously:
   - Primary: `owslib.wms.WebMapService` or `owslib.wmts.WebMapTileService`
   - Fallback: `wmts_parser.parse_wmts_capabilities()` for non-standard XML
9. **Vector tile loading**: User loads a vector basemap → `VectorTileLoadTask` downloads style JSON in background, then `QgsVectorTileLayer` loads with the style on the main thread
10. User clicks Load → creates `QgsRasterLayer` (raster) or `QgsVectorTileLayer` (vector) from datasource URI
11. User modifications save to individual YAML files in `resources/providers/user/`; tag edits save to `tag_overrides.yaml` or directly to the provider file depending on user preference
12. Provider deletions remove the corresponding YAML file; renames clean up old filenames via `source_file` tracking

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
- Standard QGIS/Qt APIs (`qgis.core`, `qgis.PyQt`)

## Network Requests

**All HTTP requests must use QGIS's built-in Qt network stack.** Do **not** use Python's `requests`, `urllib.request`, or any other Python-level HTTP library.

- **`QgsBlockingNetworkRequest`**: Primary HTTP client for synchronous requests from any thread (main thread or `QgsTask` worker threads). Supports HTTP/2, TLS 1.3, and uses QGIS proxy/auth configuration automatically.
- **`QNetworkRequest`** / **`QUrl`**: Used to construct request objects passed to `QgsBlockingNetworkRequest`.
- **OWSLib**: Used for WMS/WMTS XML parsing only — always pre-fetch the capabilities XML with `QgsBlockingNetworkRequest` and pass it via the `xml=` parameter so OWSLib never makes its own HTTP requests.

Typical pattern:

```python
from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtCore import QUrl

req = QgsBlockingNetworkRequest()
qreq = QNetworkRequest(QUrl(url))
qreq.setRawHeader(b"User-Agent", b"...")
error = req.get(qreq, True)
if error != QgsBlockingNetworkRequest.NoError:
    raise RuntimeError(f"Network error {error}: {req.errorMessage()}")
content = bytes(req.reply().content()).decode("utf-8")
```

**Why**: Python's `requests` and `urllib` do not support HTTP/2. Some tile servers (e.g. EOX) negotiate HTTP/2 exclusively, causing `SSLEOFError` / `UNEXPECTED_EOF_WHILE_READING` failures with Python HTTP libraries. Qt's network stack handles HTTP/2 transparently.

## Configuration File Format

**YAML structure** (all configs use this format):

```yaml
# XYZ raster basemap
xyz:
  ProviderName:
    icon: icons/provider.svg
    basemaps:
      - name: Basemap Name
        url: https://example.com/tile/{z}/{x}/{y}.png
        tags: [Satellite]  # optional: All, Satellite, Streets, Terrain, Thematic, Overlay, Overlay/*

# XYZ vector tile basemap
xyz:
  ProviderName:
    icon: icons/provider.svg
    basemaps:
      - name: Vector Basemap
        tile_type: vector
        url: https://example.com/tile/{z}/{x}/{y}.pbf
        style_url: https://example.com/style.json
        tags: [Streets]

# WMS/WMTS provider (layers fetched from capabilities)
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
        tags: [Satellite]
```

**File naming convention**:
- Default configs: `{xyz|wms}_##_ProviderName.yaml` (e.g., `xyz_01_Esri.yaml`)
- User configs: `{xyz|wms}_ProviderName.yaml`

## Asynchronous WMS/WMTS Fetching

When users add WMS/WMTS providers, capabilities are fetched asynchronously:

1. **[`wms_fetch_task.py:91`](wms_fetch_task.py#L91)**: `WMSFetchTask` extends `QgsTask` for background processing
2. Runs in worker thread via `QgsApplication.taskManager().addTask()`
3. Auto-detects service type (WMS vs WMTS) from URL
4. **Capabilities XML is fetched with `QgsBlockingNetworkRequest`** (Qt network stack, supports HTTP/2), then passed to parsers
5. **Dual parsing strategy**:
   - Try OWSLib first (standard-compliant services) — XML passed via `xml=` parameter
   - Fallback to [`wmts_parser.py`](wmts_parser.py) ElementTree parser (handles non-standard XML, namespace issues)
6. Emits `finished` signal with `FetchResult` dataclass on completion
7. UI remains responsive during network operations

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
- Supported languages: zh (Chinese), ja (Japanese), ko (Korean), fr (French), de (German), ru (Russian), ar (Arabic), es (Spanish), pt (Brazilian Portuguese), hi (Hindi), bn (Bangla)

**Note**: The `i18n/Basemaps.pro` file lists all source files with translatable strings. When adding new Python files with user-facing messages, add them to the `SOURCES` list in this file.

### Tag Translation Special Case

Tag strings (`All`, `Satellite`, `Streets`, `Terrain`, `Thematic`, `Overlay`, `Overlay/Hydrography`, `Overlay/Transportation`, `Overlay/Labels`, `Overlay/Boundaries`) are defined as Python constants in `basemaps_dialog.py` (`AVAILABLE_TAGS`, `ASSIGNABLE_TAGS`), not `tr()` literals. `pylupdate5` cannot extract them and marks them `type="obsolete"` in `.ts` files. At runtime, `QCoreApplication.translate("BasemapsDialog", tag)` requires these strings to be active (not obsolete).

**Required 3-step workflow after every `pylupdate5` run:**

```bash
# 1. Extract new strings
pylupdate5 i18n/Basemaps.pro

# 2. Restore tag translations from obsolete (must run after every pylupdate5)
conda run -n qgis39 python3 -c "
import re, os
I18N = 'i18n'
TAGS = ['All','Satellite','Streets','Terrain','Thematic','Overlay',
        'Overlay/Hydrography','Overlay/Transportation','Overlay/Labels','Overlay/Boundaries',
        'Default Providers ────────────────','User Providers ─────────────────']
for lang in ['zh','ja','fr','de','ru','ko','ar','es','pt','hi','bn']:
    for prefix in [f'basemaps_{lang}.ts', f'Basemaps_{lang}.ts']:
        fp = os.path.join(I18N, prefix)
        if not os.path.exists(fp): continue
        c = open(fp, encoding='utf-8').read()
        for t in TAGS:
            c = c.replace(f'<source>{t}</source>\n        <translation type=\"obsolete\">',
                          f'<source>{t}</source>\n        <translation>')
        # Also remove any @default context block (bad entries from tr() misuse)
        c = re.sub(r'<context>\s*<name>@default</name>.*?</context>', '', c, flags=re.DOTALL)
        open(fp, 'w', encoding='utf-8').write(c)
"

# 3. Compile
lrelease i18n/Basemaps.pro
```

**Do NOT skip step 2** — without it, tag badges in the gallery view will display untranslated English strings.

## Preview System

Preview thumbnails are managed by [`preview_manager.py`](preview_manager.py):

1. **Raster tiles**: Multi-zoom escalation (z=0 → z=0 composite → z=1 composite → ... up to z=3) with per-tile retry (5 attempts)
2. **Vector tiles**: Off-screen `QgsMapRendererSequentialJob` rendering with zoom extent retries (z=0 to z=4), blank preview detection via color histogram, placeholder generation with QPainter
3. **Wayback optimization**: All ESRI Wayback layers share a single preview (same imagery, different dates)
4. **Caching**: PNG files stored in `resources/previews/{default|user}/{xyz|wms}/`, keyed by provider/basemap name
5. **Network queue**: Configurable concurrency based on CPU count (min 2, max 8), single-tile and composite-tile queues
6. **Vector style pipeline**: Fetches style JSON, detects Mapbox style vs TileJSON, builds generic styles from TileJSON with color palettes per source layer

## UI Features

- **Gallery card view**: Custom `BasemapCardDelegate` renders 140×100px cards with preview thumbnails, provider fallback icons, colored tag badges, protocol badges, hover expansion
- **Tag badge colors**: Satellite=#4A90E2, Streets=#E67E22, Terrain=#27AE60, Thematic=#8E44AD, Overlay=#1ABC9C, Overlay/Hydrography=#3498DB, Overlay/Transportation=#F39C12, Overlay/Labels=#E91E63, Overlay/Boundaries=#795548
- **Chunked rendering**: Basemap lists populate in chunks of 15 items per `QTimer` tick with version-guarded deferred processing to keep UI responsive for providers with 170+ layers
- **Bidirectional view sync**: Text (list/tree) and Gallery (grid) views synchronized across both XYZ and WMS tabs
- **Search + tag filter**: Real-time text search on layer names combined with tag category combo box filtering; providers without matching items are hidden
- **Context menus**: Right-click on default providers to "Duplicate as User Provider" for customization

## Translation Conventions

When editing translations, follow terminology conventions for consistency.

**General rules**:

- Use domain-standard GIS terminology for each language (e.g., QGIS official translations, Esri localizations)
- Keep terminology consistent within each language file — never mix synonyms for the same concept
- Prefer shorter forms when both are acceptable (e.g., ja: レイヤ over レイヤー)
- Portuguese uses Brazilian (pt-BR) conventions exclusively

## Contributing

- GitHub: https://github.com/Fanchengyan/Basemaps
- Issues: https://github.com/Fanchengyan/Basemaps/issues

# Basemaps (Plugin for QGIS)

A QGIS plugin that provides easy access to multiple well-known online basemap services, supporting XYZ tiles, WMS, and WMTS services.

## Features

- **Pre-integrated Popular Basemap Providers**: Includes well-known basemaps such as `ESRI`, `Google Maps`, `Bing Maps`, `OpenStreetMap`, `Sentinel-2 Cloudless`, and more.
- **Wide Compatibility**: Fully supports **QGIS 3.x** and **QGIS 4.x** versions.
- **Multi-language Support**: Available in multiple languages including `English`, `Chinese (Simplified)`, `Japanese`, `Korean`, `French`, `German`, `Russian`, and `Arabic`.

- **Intuitive Management**: Easily add, edit, and remove custom basemap services.
- **Import/Export Functionality**: Share your custom basemap configurations with others easily using the import/export feature (supports ZIP files).
- **Asynchronous Loading**: Fetches WMS/WMTS capabilities in the background ensuring the UI remains responsive.


## Installation

### Via QGIS Plugin Manager (Recommended)
1. Open QGIS.
2. Go to `Plugins` -> `Manage and Install Plugins...`.
3. Select the `All` tab.
4. Search for `Basemaps`.
5. Click `Install Plugin`.

### Manual Installation
1. Download the source code.
2. Extract the folder to your QGIS plugins directory:
   - **Windows**: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **macOS**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

## Usage

1. **Open the Plugin** either by: 
   - Click the **Basemaps** icon in the toolbar 
   - or go to `Plugins` -> `Basemap Management` -> `Load Basemaps`.
2. **Load Basemaps**: 
   1. Browse and select a provider in `XYZ Tiles` or `WMS/WMTS` tab
   2. Select the desired Basemaps or Layers (hold `Shift` or `Ctrl`/`Command` to select multiple), then click `Load` to add them to your project.
3. **Manage Providers**:
   - **Add**: Click the `Add` button to add a new custom provider.
   - **Edit**: Right-click on a user-defined provider to edit it.
   - **Remove**: Click the `Remove` button to remove it.
   - **Duplicated as user provider**: Right-click on a default provider, then click `Duplicated as user provider` to duplicate it as a user provider. Then, you can edit the duplicated provider.
4. **Import/Export**: 
   - **Import**: Click the `Import` button to import a custom provider.
   - **Export**: Click the `Export` button to export a custom provider.

## Screenshots

- **XYZ Services:**
![Basemaps Plugin](docs/imgs/Basemaps_xyz.png)

- **WMS Services:**
![Basemaps Plugin](docs/imgs/Basemaps_wms.png)

## Contributing

Contributions are welcome! If you have any ideas or suggestions, please feel free to:

- [open issues](https://github.com/Fanchengyan/Basemaps/issues)
- [submit pull requests](https://github.com/Fanchengyan/Basemaps/pulls)
- share other basemap services on [Discussions](https://github.com/Fanchengyan/Basemaps/discussions)

"""Configuration loader module for Basemaps plugin.

Supports YAML formats with automatic format detection.
Provides unified interface for loading configuration files.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal

import yaml

from .messageTool import Logger


def load_config_file(
    filepath: str | Path,
) -> dict[str, list[dict[str, Any]]]:
    """Load configuration file (YAML) and return providers list.

    Parameters
    ----------
    filepath : str | Path
        Path to configuration file

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Dictionary with 'providers' key containing list of provider configs

    Raises
    ------
    FileNotFoundError
        If file does not exist
    ValueError
        If file format is not supported or invalid
    """
    filepath = Path(filepath)

    if not filepath.exists():
        Logger.critical(f"Configuration file not found: {filepath}")
        raise FileNotFoundError(f"Configuration file not found: {filepath}")

    # Detect file format by extension
    suffix = filepath.suffix.lower()
    if suffix in [".yaml", ".yml"]:
        return _load_yaml_config(filepath)
    else:
        Logger.critical(f"Unsupported file format: {suffix}")
        raise ValueError(f"Unsupported file format: {suffix}. Use .yaml, or .yml")


def _load_yaml_config(filepath: Path) -> dict[str, list[dict[str, Any]]]:
    """Load YAML configuration file and convert to providers list.

    Parameters
    ----------
    filepath : Path
        Path to YAML file

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        Dictionary with 'providers' key containing list of providers
    """
    Logger.info(f"Loading YAML config: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Convert YAML type-based structure to providers list
    providers = _convert_yaml_to_providers(data)

    return {"providers": providers}


def _convert_yaml_to_providers(
    yaml_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert YAML type-based structure to flat providers list.

    Parameters
    ----------
    yaml_data : dict[str, dict[str, Any]]
        YAML data with type as top-level keys (xyz, wms)

    Returns
    -------
    list[dict[str, Any]]
        Flat list of provider dictionaries
    """
    providers = []

    # Process each type (xyz, wms, etc.)
    for type_name in ["xyz", "wms"]:
        providers_dict = yaml_data.get(type_name, {})

        for provider_name, provider_config in providers_dict.items():
            provider: dict[str, Any] = {
                "name": provider_name,
                "type": type_name,
                "icon": provider_config.get("icon", ""),
            }

            # Preserve created_at timestamp if it exists
            if "created_at" in provider_config:
                provider["created_at"] = provider_config["created_at"]

            # Preserve token if it exists
            if "token" in provider_config:
                provider["token"] = provider_config["token"]
            if "token_param" in provider_config:
                provider["token_param"] = provider_config["token_param"]

            # Add type-specific fields
            if type_name == "xyz":
                provider["basemaps"] = provider_config.get("basemaps", [])
            elif type_name == "wms":
                provider["url"] = provider_config.get("url", "")
                provider["layers"] = provider_config.get("layers", [])
                if "service_type" in provider_config:
                    provider["service_type"] = provider_config["service_type"]

            providers.append(provider)

    return providers


def save_config_as_yaml(
    filepath: str | Path,
    providers: list[dict[str, Any]],
) -> None:
    """Save providers to YAML file with type-based organization.

    Parameters
    ----------
    filepath : str | Path
        Output YAML file path
    providers : list[dict[str, Any]]
        List of provider dictionaries to save
    """
    filepath = Path(filepath)

    # Convert providers list to type-based structure
    yaml_data: dict[str, dict[str, Any]] = {}

    for provider in providers:
        type_name = provider.get("type")
        if type_name == "separator":
            continue

        if type_name not in yaml_data:
            yaml_data[type_name] = {}

        provider_name = provider["name"]
        provider_config: dict[str, Any] = {"icon": provider.get("icon", "")}

        # Preserve created_at timestamp if it exists
        if "created_at" in provider:
            provider_config["created_at"] = provider["created_at"]

        # Preserve token if it exists
        if "token" in provider:
            provider_config["token"] = provider["token"]
        if "token_param" in provider:
            provider_config["token_param"] = provider["token_param"]

        if type_name == "xyz":
            provider_config["basemaps"] = provider.get("basemaps", [])
        elif type_name == "wms":
            provider_config["url"] = provider.get("url", "")

            # service_type at provider level (not in each layer)
            if "service_type" in provider:
                provider_config["service_type"] = provider["service_type"]

            # Normalize layer field order using OrderedDict
            # Enforce standard order: layer_name, layer_title, crs, format, styles
            # NOTE: service_type is at provider level, not layer level
            layers = provider.get("layers", [])
            normalized_layers = []
            for layer in layers:
                # Use OrderedDict to ensure consistent field order
                ordered_layer = OrderedDict()
                # IMPORTANT: layer_name must be first
                if "layer_name" in layer:
                    ordered_layer["layer_name"] = layer["layer_name"]
                if "layer_title" in layer:
                    ordered_layer["layer_title"] = layer["layer_title"]
                # Then: technical properties in standard order
                if "crs" in layer:
                    ordered_layer["crs"] = layer["crs"]
                if "format" in layer:
                    ordered_layer["format"] = layer["format"]
                if "styles" in layer:
                    ordered_layer["styles"] = layer["styles"]
                if "tags" in layer:
                    ordered_layer["tags"] = layer["tags"]
                # Add any other fields except service_type (which is at provider level)
                for key, value in layer.items():
                    if key not in ordered_layer and key != "service_type":
                        ordered_layer[key] = value
                normalized_layers.append(ordered_layer)

            provider_config["layers"] = normalized_layers

        yaml_data[type_name][provider_name] = provider_config

    # Save to file with custom representer for better formatting
    Logger.info(f"Saving YAML config to {filepath}")

    # Use a custom Dumper to maintain field order
    class OrderedDumper(yaml.SafeDumper):
        pass

    def dict_representer(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

    OrderedDumper.add_representer(dict, dict_representer)
    OrderedDumper.add_representer(OrderedDict, ordered_dict_representer)

    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(
            yaml_data,
            f,
            Dumper=OrderedDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )

    Logger.info(f"Successfully saved {len(providers)} providers to {filepath}")


def _build_provider_yaml_data(provider: dict[str, Any]) -> dict[str, Any]:
    """Build the YAML data structure for a single provider.

    Returns a dict suitable for yaml.dump: ``{type: {provider_name: {...}}}``.
    """
    provider_type = provider.get("type")
    provider_name = provider.get("name", "unknown")

    if provider_type not in ["xyz", "wms"]:
        raise ValueError(f"Invalid provider type: {provider_type}")

    yaml_data: dict[str, Any] = {provider_type: {provider_name: {}}}
    provider_config = yaml_data[provider_type][provider_name]

    provider_config["icon"] = provider.get("icon", "")

    if "created_at" in provider:
        provider_config["created_at"] = provider["created_at"]
    if "token" in provider:
        provider_config["token"] = provider["token"]
    if "token_param" in provider:
        provider_config["token_param"] = provider["token_param"]

    if provider_type == "xyz":
        basemaps = provider.get("basemaps", [])
        normalized_basemaps = []
        for bm in basemaps:
            ordered_bm = OrderedDict()
            if "name" in bm:
                ordered_bm["name"] = bm["name"]
            if "url" in bm:
                ordered_bm["url"] = bm["url"]
            if "tags" in bm:
                ordered_bm["tags"] = bm["tags"]
            for key, value in bm.items():
                if key not in ordered_bm:
                    ordered_bm[key] = value
            normalized_basemaps.append(ordered_bm)
        provider_config["basemaps"] = normalized_basemaps
    elif provider_type == "wms":
        provider_config["url"] = provider.get("url", "")
        if "service_type" in provider:
            provider_config["service_type"] = provider["service_type"]
        layers = provider.get("layers", [])
        normalized_layers = []
        for layer in layers:
            ordered_layer = OrderedDict()
            if "layer_name" in layer:
                ordered_layer["layer_name"] = layer["layer_name"]
            if "layer_title" in layer:
                ordered_layer["layer_title"] = layer["layer_title"]
            if "crs" in layer:
                ordered_layer["crs"] = layer["crs"]
            if "format" in layer:
                ordered_layer["format"] = layer["format"]
            if "styles" in layer:
                ordered_layer["styles"] = layer["styles"]
            if "tags" in layer:
                ordered_layer["tags"] = layer["tags"]
            for key, value in layer.items():
                if key not in ordered_layer and key != "service_type":
                    ordered_layer[key] = value
            normalized_layers.append(ordered_layer)
        provider_config["layers"] = normalized_layers

    return yaml_data


def _write_provider_yaml(filepath: Path, yaml_data: dict[str, Any]) -> None:
    """Write provider YAML data to a file, preserving OrderedDict order."""

    class OrderedDumper(yaml.SafeDumper):
        pass

    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

    OrderedDumper.add_representer(OrderedDict, ordered_dict_representer)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(
            yaml_data,
            f,
            Dumper=OrderedDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )


def save_provider_to_path(
    filepath: str | Path,
    provider: dict[str, Any],
) -> None:
    """Save a provider to a specific YAML file path.

    Parameters
    ----------
    filepath : str | Path
        Exact output file path.
    provider : dict[str, Any]
        Provider configuration dictionary.
    """
    filepath = Path(filepath)
    provider_name = provider.get("name", "unknown")
    yaml_data = _build_provider_yaml_data(provider)
    _write_provider_yaml(filepath, yaml_data)
    Logger.info(f"Saved provider '{provider_name}' to {filepath}")
    if isinstance(provider, dict):
        provider["source_file"] = str(filepath.resolve())


def save_provider_to_yaml(
    directory: Path,
    provider: dict[str, Any],
    prefix: Literal["default", "user"] = "user",
) -> Path | None:
    """Save a single provider to its own YAML file (auto-generated path).

    Parameters
    ----------
    directory : Path
        Base directory (usually resources/).
    provider : dict[str, Any]
        Provider configuration dictionary.
    prefix : Literal['default', 'user']
        File prefix ('default' or 'user').

    Returns
    -------
    Path | None
        Path to the saved file, or None if skipped.
    """
    provider_type = provider.get("type")
    provider_name = provider.get("name", "unknown")

    if provider_type == "separator":
        Logger.info("Skipping separator provider", notify_user=False)
        return None

    if provider_type not in ["xyz", "wms"]:
        raise ValueError(f"Invalid provider type: {provider_type}")

    providers_dir = directory / "providers" / prefix
    safe_name = (
        provider_name.replace(" ", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )
    filename = f"{provider_type}_{safe_name}.yaml"
    filepath = providers_dir / filename

    # If the provider was renamed, delete the old file
    old_source = provider.get("source_file")
    if old_source and Path(old_source).resolve() != filepath.resolve():
        try:
            Path(old_source).unlink(missing_ok=True)
            Logger.info(f"Removed old provider file: {old_source}")
        except OSError as e:
            Logger.warning(f"Failed to remove old provider file '{old_source}': {e}")

    yaml_data = _build_provider_yaml_data(provider)
    _write_provider_yaml(filepath, yaml_data)

    Logger.info(f"Saved provider '{provider_name}' to {filepath}")
    if isinstance(provider, dict):
        provider["source_file"] = str(filepath.resolve())

    return filepath


def save_providers_separately(
    directory: Path,
    providers: list[dict[str, Any]],
    prefix: Literal["default", "user"] = "user",
) -> list[Path]:
    """Save multiple providers, each to its own file.

    Parameters
    ----------
    directory : Path
        Directory to save files in
    providers : list[dict[str, Any]]
        List of provider dictionaries
    prefix : Literal['default', 'user']
        File prefix ('default' or 'user')

    Returns
    -------
    saved_files : list[Path]
        List of saved file paths
    """
    saved_files = []

    for provider in providers:
        if provider.get("type") == "separator":
            continue

        try:
            filepath = save_provider_to_yaml(directory, provider, prefix)
            if filepath:
                saved_files.append(filepath)
        except Exception as e:
            Logger.critical(f"Failed to save provider '{provider.get('name')}': {e}")
            continue

    Logger.info(f"Saved {len(saved_files)} providers to {directory}")
    return saved_files


def load_all_provider_files(
    directory: Path,
    prefix: Literal["default", "user"] = "default",
) -> list[dict[str, Any]]:
    """Load all provider files with given prefix from directory.

    Parameters
    ----------
    directory : Path
        Base directory (usually resources/)
    prefix : Literal['default', 'user']
        Subdirectory name to load from ('default' or 'user')

    Returns
    -------
    list[dict[str, Any]]
        Combined list of all providers from matched files

    Notes
    -----
    Loads from: directory/providers/{prefix}/*.yaml
    Falls back to old location: directory/{prefix}_*.yaml for backward compatibility
    """
    providers = []
    # Try new directory structure first: resources/providers/{prefix}/
    new_providers_dir = directory / "providers" / prefix
    if new_providers_dir.exists():
        Logger.info(f"Loading providers from new structure: {new_providers_dir}")
        for yaml_file in sorted(new_providers_dir.glob("*.yaml")):
            try:
                data = load_config_file(yaml_file)
                file_providers = data.get("providers", [])

                # Add source file path to each provider
                for provider in file_providers:
                    provider["source_file"] = str(yaml_file.resolve())

                providers.extend(file_providers)
                Logger.info(
                    f"Loaded {len(file_providers)} provider(s) from {yaml_file.name}"
                )
            except Exception as e:
                Logger.critical(f"Failed to load {yaml_file}: {e}")
                continue

    return providers


def delete_provider_file(
    directory: Path,
    provider: dict[str, Any],
    prefix: Literal["default", "user"] = "user",
) -> bool:
    """Delete the YAML file for a specific provider.

    Parameters
    ----------
    directory : Path
        Base directory (usually resources/)
    provider : dict[str, Any]
        Provider configuration dictionary
    prefix : Literal['default', 'user']
        Subdirectory name ('default' or 'user')

    Returns
    -------
    bool
        True if file was deleted, False if file not found

    Notes
    -----
    Tries to delete from new structure: directory/providers/{prefix}/{type}_{safe_name}.yaml
    Falls back to old location: directory/{prefix}_{type}_{safe_name}.yaml
    """
    provider_type = provider.get("type")
    provider_name = provider.get("name", "unknown")

    if provider_type == "separator":
        Logger.info("Skipping separator provider", notify_user=False)
        return False

    if provider_type not in ["xyz", "wms"]:
        Logger.warning(f"Invalid provider type: {provider_type}")
        return False

    # Create safe filename from provider name
    safe_name = (
        provider_name.replace(" ", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )

    # Safety check - verify source_file matches expected path
    expected_new_path = (
        directory / "providers" / prefix / f"{provider_type}_{safe_name}.yaml"
    )
    if "source_file" in provider:
        source_path = Path(provider["source_file"]).resolve()
        # Only delete if source_file matches expected location
        if source_path != expected_new_path.resolve():
            Logger.warning(
                f"Source file mismatch: expected {expected_new_path}, "
                f"but provider has {source_path}. Proceeding with expected path."
            )

    # Try new structure first: resources/providers/{prefix}/{type}_{safe_name}.yaml
    new_filename = f"{provider_type}_{safe_name}.yaml"
    new_filepath = directory / "providers" / prefix / new_filename

    if new_filepath.exists():
        try:
            new_filepath.unlink()
            Logger.info(f"Deleted provider file: {new_filepath}")
            return True
        except Exception as e:
            Logger.critical(f"Failed to delete provider file {new_filepath}: {e}")
            return False

    # Fall back to old structure: resources/{prefix}_{type}_{safe_name}.yaml
    old_filename = f"{prefix}_{provider_type}_{safe_name}.yaml"
    old_filepath = directory / old_filename

    if old_filepath.exists():
        try:
            old_filepath.unlink()
            Logger.info(f"Deleted provider file (old structure): {old_filepath}")
            return True
        except Exception as e:
            Logger.critical(f"Failed to delete provider file {old_filepath}: {e}")
            return False

    Logger.warning(f"Provider file not found: {new_filepath} or {old_filepath}")
    return False


# ---------------------------------------------------------------------------
# Tag overrides – persist tag edits on default (built-in) provider items
# ---------------------------------------------------------------------------

TAG_OVERRIDES_FILENAME = "tag_overrides.yaml"


def load_tag_overrides(
    resources_dir: Path,
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Load tag overrides for default-provider basemaps and layers.

    Parameters
    ----------
    resources_dir : Path
        Base resources directory.

    Returns
    -------
    dict
        Nested dict: ``{type: {provider_name: {item_name: {"tags": [...]}}}}``.
        *type* is ``"xyz"`` or ``"wms"``.  *item_name* is a basemap ``name``
        (XYZ) or a ``layer_name`` (WMS/WMTS).
    """
    overrides_file = resources_dir / TAG_OVERRIDES_FILENAME
    if not overrides_file.exists():
        return {}

    try:
        with open(overrides_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception:
        Logger.warning(f"Failed to load tag overrides from {overrides_file}")
        return {}


def save_tag_overrides(
    resources_dir: Path,
    overrides: dict[str, dict[str, dict[str, list[str]]]],
) -> None:
    """Persist tag overrides to disk.

    Parameters
    ----------
    resources_dir : Path
        Base resources directory.
    overrides : dict
        Same structure as returned by :func:`load_tag_overrides`.
    """
    overrides_file = resources_dir / TAG_OVERRIDES_FILENAME

    # Remove empty keys recursively
    def _prune_empty(d: dict) -> dict:
        result = {}
        for k, v in d.items():
            if isinstance(v, dict):
                v = _prune_empty(v)
                if v:
                    result[k] = v
            elif v:
                result[k] = v
        return result

    overrides = _prune_empty(overrides)

    if not overrides:
        if overrides_file.exists():
            overrides_file.unlink()
            Logger.info("Removed empty tag overrides file")
        return

    with open(overrides_file, "w", encoding="utf-8") as f:
        yaml.dump(
            overrides,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )
    Logger.info(f"Saved tag overrides to {overrides_file}")


def apply_tag_overrides(
    providers: list[dict[str, Any]],
    overrides: dict[str, dict[str, dict[str, list[str]]]],
) -> None:
    """Apply persisted tag overrides to providers **in-place**.

    Parameters
    ----------
    providers : list[dict]
        Flat provider list (already loaded from YAML files).
    overrides : dict
        Tag overrides as returned by :func:`load_tag_overrides`.
    """
    if not overrides:
        return

    for provider in providers:
        provider_type = provider.get("type")
        provider_name = provider.get("name")
        if not provider_type or not provider_name:
            continue

        provider_overrides = overrides.get(provider_type, {}).get(provider_name, {})
        if not provider_overrides:
            continue

        if provider_type == "xyz":
            for bm in provider.get("basemaps", []):
                bm_name = bm.get("name")
                if bm_name and bm_name in provider_overrides:
                    bm["tags"] = list(provider_overrides[bm_name].get("tags", []))
        elif provider_type == "wms":
            for layer in provider.get("layers", []):
                layer_name = layer.get("layer_name")
                if layer_name and layer_name in provider_overrides:
                    layer["tags"] = list(provider_overrides[layer_name].get("tags", []))

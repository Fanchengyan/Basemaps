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


def save_provider_to_yaml(
    directory: Path,
    provider: dict[str, Any],
    prefix: Literal["default", "user"] = "user",
) -> Path | None:
    """Save a single provider to its own YAML file.

    Parameters
    ----------
    directory : Path
        Base directory (usually resources/)
    provider : dict[str, Any]
        Provider configuration dictionary
    prefix : Literal['default', 'user']
        File prefix ('default' or 'user')

    Returns
    -------
    Path | None
        Path to the saved file, or None if skipped
    """
    provider_type = provider.get("type")
    provider_name = provider.get("name", "unknown")

    if provider_type == "separator":
        Logger.info("Skipping separator provider", notify_user=False)
        return None

    if provider_type not in ["xyz", "wms"]:
        raise ValueError(f"Invalid provider type: {provider_type}")

    # Create directory structure: resources/providers/{prefix}/
    providers_dir = directory / "providers" / prefix
    providers_dir.mkdir(parents=True, exist_ok=True)

    # Create safe filename from provider name
    safe_name = (
        provider_name.replace(" ", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )
    filename = f"{provider_type}_{safe_name}.yaml"
    filepath = providers_dir / filename

    # Create single-provider YAML structure
    yaml_data = {provider_type: {provider_name: {}}}
    provider_config = yaml_data[provider_type][provider_name]

    provider_config["icon"] = provider.get("icon", "")

    # Preserve created_at timestamp if it exists
    if "created_at" in provider:
        provider_config["created_at"] = provider["created_at"]

    if provider_type == "xyz":
        provider_config["basemaps"] = provider.get("basemaps", [])
    elif provider_type == "wms":
        provider_config["url"] = provider.get("url", "")

        # service_type at provider level (not in each layer)
        if "service_type" in provider:
            provider_config["service_type"] = provider["service_type"]

        # Normalize layer field order
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
            # Remove service_type from layer level - it's at provider level now
            # Add any other fields except service_type
            for key, value in layer.items():
                if key not in ordered_layer and key != "service_type":
                    ordered_layer[key] = value
            normalized_layers.append(ordered_layer)

        provider_config["layers"] = normalized_layers

    # Save to file
    class OrderedDumper(yaml.SafeDumper):
        pass

    def ordered_dict_representer(dumper, data):
        return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

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

    Logger.info(f"Saved provider '{provider_name}' to {filepath}")

    # Update provider's source_file to reflect new location
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

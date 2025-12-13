# Copyright (C) 2025  Chengyan (Fancy) Fan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""WMTS capabilities XML parser using ElementTree.

This module provides a fallback parser for WMTS services that may have
non-standard XML structures or namespace issues that OWSLib cannot handle.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional


def parse_wmts_capabilities(xml_content: bytes | str) -> list[dict]:
    """
    Parse WMTS capabilities XML and extract layer information.

    Parameters
    ----------
    xml_content : bytes | str
        The WMTS capabilities XML content.

    Returns
    -------
    list[dict]
        A list of layer information dictionaries.

    Raises
    ------
    ValueError
        If no layers are found in the capabilities document.
    """
    if isinstance(xml_content, str):
        xml_content = xml_content.encode('utf-8')

    root = ET.fromstring(xml_content)
    layers = []

    # Find all Layer elements, regardless of namespace
    for layer_elem in root.iter():
        if layer_elem.tag.endswith('}Layer') or layer_elem.tag == 'Layer':
            layer_info = _parse_layer_element(layer_elem)
            if layer_info:
                layers.append(layer_info)

    if not layers:
        raise ValueError("No layers found in WMTS capabilities")

    return layers


def _parse_layer_element(layer_elem: ET.Element) -> Optional[dict]:
    """
    Parse a single WMTS Layer element.

    Parameters
    ----------
    layer_elem : ET.Element
        The Layer XML element.

    Returns
    -------
    dict | None
        Layer information dictionary or None if layer_name is missing.
    """
    # Get Identifier (layer name)
    layer_name = _find_text(layer_elem, 'Identifier')
    if not layer_name:
        return None

    # Get Title
    layer_title = _find_text(layer_elem, 'Title') or layer_name

    # Get TileMatrixSet links
    tile_matrix_sets = _find_all_text(layer_elem, 'TileMatrixSet')

    # Get available formats
    formats = _find_all_text(layer_elem, 'Format')

    # Get available styles
    styles = _extract_styles(layer_elem)

    return {
        "layer_name": layer_name,
        "layer_title": layer_title,
        "crs": tile_matrix_sets or ["GoogleMapsCompatible"],
        "format": formats or ["image/jpeg"],
        "styles": styles,
        "service_type": "wmts",
    }


def _find_text(elem: ET.Element, local_name: str) -> Optional[str]:
    """
    Find the first child element with given local name and return its text.

    Parameters
    ----------
    elem : ET.Element
        The element to search in.
    local_name : str
        The local name of the element to find (without namespace).

    Returns
    -------
    str | None
        The text content of the element or None if not found.
    """
    for child in elem.iter():
        if child.tag.endswith('}' + local_name) or child.tag == local_name:
            return child.text
    return None


def _find_all_text(elem: ET.Element, local_name: str) -> list[str]:
    """
    Find all child elements with given local name and return their texts.

    Parameters
    ----------
    elem : ET.Element
        The element to search in.
    local_name : str
        The local name of the elements to find (without namespace).

    Returns
    -------
    list[str]
        A list of text contents from matching elements.
    """
    results = []
    for child in elem.iter():
        if child.tag.endswith('}' + local_name) or child.tag == local_name:
            if child.text:
                results.append(child.text)
    return results


def _extract_styles(layer_elem: ET.Element) -> list[str]:
    """
    Extract style identifiers from a Layer element.

    Parameters
    ----------
    layer_elem : ET.Element
        The Layer XML element.

    Returns
    -------
    list[str]
        A list of style identifiers.
    """
    styles = []
    for child in layer_elem.iter():
        if child.tag.endswith('}Style') or child.tag == 'Style':
            style_id = _find_text(child, 'Identifier')
            if style_id:
                styles.append(style_id)
    return styles

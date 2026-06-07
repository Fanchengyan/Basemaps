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

"""WMTS capabilities XML parser using the standard-library Expat parser.

This module provides a fallback parser for WMTS services that may have
non-standard XML structures or namespace issues that OWSLib cannot handle.
It uses event-based parsing and rejects DTD/entity declarations so untrusted
capabilities documents are not parsed through vulnerable tree-building APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from xml.parsers import expat

from qgis.PyQt.QtCore import QCoreApplication

from .messageTool import Logger


@dataclass
class _LayerBuilder:
    """Collect WMTS layer fields while parsing a single ``Layer`` element.

    Attributes
    ----------
    layer_name : str | None
        WMTS layer identifier.
    layer_title : str | None
        Human-readable layer title.
    tile_matrix_sets : list[str]
        Tile matrix set identifiers supported by the layer.
    formats : list[str]
        Image formats supported by the layer.
    styles : list[str]
        Style identifiers supported by the layer.
    resource_url : str | None
        RESTful WMTS tile template, when advertised by the service.
    style_depth : int
        Current nested style element depth.
    """

    layer_name: str | None = None
    layer_title: str | None = None
    tile_matrix_sets: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    resource_url: str | None = None
    style_depth: int = 0

    def to_layer(self) -> dict | None:
        """Convert collected values into the plugin layer dictionary.

        Returns
        -------
        dict | None
            Layer information dictionary, or ``None`` when no layer
            identifier was found.
        """
        if not self.layer_name:
            return None

        return {
            "layer_name": self.layer_name,
            "layer_title": self.layer_title or self.layer_name,
            "crs": self.tile_matrix_sets or ["GoogleMapsCompatible"],
            "format": self.formats or ["image/jpeg"],
            "styles": self.styles,
            "service_type": "wmts",
            "resource_url": self.resource_url,
        }


class _WmtsCapabilitiesHandler:
    """Event handler that extracts WMTS layers from an Expat parse stream.

    Attributes
    ----------
    layers : list[dict]
        Parsed WMTS layer dictionaries.
    """

    _TEXT_ELEMENTS = {"Identifier", "Title", "Format", "TileMatrixSet"}

    def __init__(self) -> None:
        self.layers: list[dict] = []
        self._layer_stack: list[_LayerBuilder] = []
        self._element_stack: list[str] = []
        self._text_element: str | None = None
        self._text_chunks: list[str] = []

    def start_element(self, name: str, attrs: dict[str, str]) -> None:
        """Handle an XML start element event.

        Parameters
        ----------
        name : str
            Element name from Expat. Namespace-expanded names use ``}`` as
            the configured separator.
        attrs : dict[str, str]
            Element attributes.
        """
        local_name = _local_name(name)
        self._element_stack.append(local_name)

        if local_name == "Layer":
            self._layer_stack.append(_LayerBuilder())
            return

        current_layer = self._current_layer()
        if current_layer is None:
            return

        if local_name == "Style":
            current_layer.style_depth += 1
            return

        if local_name == "ResourceURL":
            resource_type = _get_attribute(attrs, "resourceType", "")
            template = _get_attribute(attrs, "template")
            if template and resource_type == "tile" and not current_layer.resource_url:
                current_layer.resource_url = template
            return

        if local_name in self._TEXT_ELEMENTS:
            self._text_element = local_name
            self._text_chunks = []

    def end_element(self, name: str) -> None:
        """Handle an XML end element event.

        Parameters
        ----------
        name : str
            Element name from Expat.
        """
        local_name = _local_name(name)
        current_layer = self._current_layer()

        if current_layer and self._text_element == local_name:
            self._store_text_value(current_layer, local_name)
            self._text_element = None
            self._text_chunks = []

        if current_layer and local_name == "Style":
            current_layer.style_depth = max(0, current_layer.style_depth - 1)

        if local_name == "Layer" and self._layer_stack:
            layer_info = self._layer_stack.pop().to_layer()
            if layer_info:
                self.layers.append(layer_info)

        if self._element_stack:
            self._element_stack.pop()

    def character_data(self, data: str) -> None:
        """Handle character data for the currently captured text element.

        Parameters
        ----------
        data : str
            Text chunk emitted by Expat.
        """
        if self._text_element and self._current_layer() is not None:
            self._text_chunks.append(data)

    def reject_doctype(self, *args: Any) -> None:
        """Reject DTD declarations in untrusted capabilities documents.

        Parameters
        ----------
        *args : Any
            Expat callback arguments.

        Raises
        ------
        ValueError
            Always raised because DTDs are not required for WMTS layer
            extraction and can enable XML entity attacks.
        """
        message = QCoreApplication.translate(
            "BasemapsPlugin",
            "DTD declarations are not supported in WMTS capabilities documents",
        )
        Logger.critical(message)
        raise ValueError(message)

    def reject_entity(self, *args: Any) -> None:
        """Reject XML entity declarations and external references.

        Parameters
        ----------
        *args : Any
            Expat callback arguments.

        Raises
        ------
        ValueError
            Always raised because entity expansion is not required for WMTS
            layer extraction.
        """
        message = QCoreApplication.translate(
            "BasemapsPlugin",
            "Entity declarations are not supported in WMTS capabilities documents",
        )
        Logger.critical(message)
        raise ValueError(message)

    def _current_layer(self) -> _LayerBuilder | None:
        """Return the active layer builder.

        Returns
        -------
        _LayerBuilder | None
            Current layer builder, or ``None`` outside a layer element.
        """
        if not self._layer_stack:
            return None
        return self._layer_stack[-1]

    def _store_text_value(self, layer: _LayerBuilder, local_name: str) -> None:
        """Store captured text on a layer builder.

        Parameters
        ----------
        layer : _LayerBuilder
            Layer builder to update.
        local_name : str
            Local XML element name for the captured value.
        """
        text_value = "".join(self._text_chunks).strip()
        if not text_value:
            return

        if local_name == "Identifier":
            if layer.style_depth > 0:
                layer.styles.append(text_value)
            elif layer.layer_name is None:
                layer.layer_name = text_value
        elif local_name == "Title" and layer.style_depth == 0 and layer.layer_title is None:
            layer.layer_title = text_value
        elif local_name == "Format":
            layer.formats.append(text_value)
        elif local_name == "TileMatrixSet":
            layer.tile_matrix_sets.append(text_value)


def parse_wmts_capabilities(xml_content: bytes | str) -> list[dict]:
    """Parse WMTS capabilities XML and extract layer information.

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
        If the capabilities document is unsafe, invalid, or contains no
        layers.
    """
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")

    handler = _WmtsCapabilitiesHandler()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = handler.start_element
    parser.EndElementHandler = handler.end_element
    parser.CharacterDataHandler = handler.character_data
    parser.StartDoctypeDeclHandler = handler.reject_doctype
    parser.EntityDeclHandler = handler.reject_entity
    parser.ExternalEntityRefHandler = handler.reject_entity

    try:
        parser.Parse(xml_content, True)
    except expat.ExpatError as error:
        message = QCoreApplication.translate(
            "BasemapsPlugin", "Invalid WMTS capabilities XML: {}"
        ).format(error)
        Logger.critical(message)
        raise ValueError(message) from error

    if not handler.layers:
        message = QCoreApplication.translate(
            "BasemapsPlugin", "No layers found in WMTS capabilities"
        )
        Logger.critical(message)
        raise ValueError(message)

    return handler.layers


def _local_name(name: str) -> str:
    """Return the local XML name from an Expat element or attribute name.

    Parameters
    ----------
    name : str
        Raw Expat name.

    Returns
    -------
    str
        Local name without namespace URI.
    """
    return name.rsplit("}", 1)[-1]


def _get_attribute(attrs: dict[str, str], local_name: str, default: str = "") -> str:
    """Get an attribute by local name from Expat attributes.

    Parameters
    ----------
    attrs : dict[str, str]
        Attribute mapping from Expat.
    local_name : str
        Local attribute name to retrieve.
    default : str, optional
        Value returned when no matching attribute exists.

    Returns
    -------
    str
        Attribute value or ``default``.
    """
    for attr_name, attr_value in attrs.items():
        if _local_name(attr_name) == local_name:
            return attr_value
    return default

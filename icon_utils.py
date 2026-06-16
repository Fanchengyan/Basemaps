"""Icon utilities for uniform rounded-rectangle provider icons."""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Default colours — kept in sync with the details-panel palette.
_DEFAULT_BG = QColor("#D5DAE1")
_DEFAULT_BORDER = QColor("#D0D5DD")


def make_rounded_icon(
    icon: QIcon | str | Path,
    size: QSize | int = 15,
    radius_ratio: float = 0.15,
    inset_ratio: float = 0.10,
    bg_color: QColor | None = _DEFAULT_BG,
    border_color: QColor | None = _DEFAULT_BORDER,
) -> QIcon:
    """Return *icon* rendered into a rounded-rectangle container.

    The container has a light background fill and a 1 px border so the
    rounded shape is clearly visible on any background colour.

    Parameters
    ----------
    icon:
        A ``QIcon``, a filesystem ``Path``, or a string path.
    size:
        Target pixel size (square).  Pass an ``int`` for a shortcut.
    radius_ratio:
        Corner radius as a fraction of *size* (default 15 %).
        At 15 px → 2 px radius; at 48 px → 7 px radius.
    inset_ratio:
        Padding between the container edge and the icon, as a fraction
        of *size* (default 10 %).  At 15 px → ~1.5 px breathing room;
        at 48 px → ~5 px.
    bg_color:
        Background fill behind the icon.  Set to ``None`` to keep the
        background transparent.
    border_color:
        0.25 px border colour.  Set to ``None`` to disable the border.
    """
    if isinstance(size, int):
        size = QSize(size, size)

    # Resolve to QIcon
    if isinstance(icon, (str, Path)):
        p = Path(icon)
        if p.exists():
            icon = QIcon(str(p))
        else:
            return QIcon()
    if not isinstance(icon, QIcon) or icon.isNull():
        return QIcon()

    # Render at 2× and tell Qt to down-scale on display → crisp result.
    scale = 2
    sz = size.width()
    render_sz = sz * scale
    pixmap = QPixmap(QSize(render_sz, render_sz))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    radius = sz * radius_ratio
    rect = QRectF(0, 0, sz, sz)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    # ── Fill background & paint icon, both clipped to the rounded rect ──
    painter.setClipPath(path)
    if bg_color is not None:
        painter.fillRect(0, 0, sz, sz, bg_color)

    # Inset icon from container edges for breathing room
    inset = int(sz * inset_ratio)
    icon.paint(painter, inset, inset, sz - inset * 2, sz - inset * 2)

    # ── Stroke the border (no clipping so the full 0.25 px is visible) ──
    if border_color is not None:
        pen_width = 0.25
        pen = QPen(border_color, pen_width)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Inset by half the pen width so the stroke stays inside the pixmap
        inset = pen_width / 2.0
        border_rect = rect.adjusted(inset, inset, -inset, -inset)
        painter.drawRoundedRect(border_rect, radius, radius)

    painter.end()

    result = QIcon(pixmap)
    result.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.Off)
    return result

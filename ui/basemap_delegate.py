from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QEvent, QModelIndex, QRect, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QStyledItemDelegate, QStyle

TAG_COLORS: dict[str, QColor] = {
    "Satellite": QColor("#4A90E2"),
    "Streets": QColor("#E67E22"),
    "Terrain": QColor("#27AE60"),
    "Thematic": QColor("#8E44AD"),
    "Overlay": QColor("#1ABC9C"),
    "Overlay/Hydrography": QColor("#3498DB"),
    "Overlay/Transportation": QColor("#F39C12"),
    "Overlay/Labels": QColor("#E91E63"),
    "Overlay/Boundaries": QColor("#795548"),
}

PROTOCOL_COLORS: dict[str, QColor] = {
    "xyz": QColor("#000000"),
    "vector": QColor("#000000"),
    "wms": QColor("#000000"),
    "wmts": QColor("#000000"),
}


class BasemapCardDelegate(QStyledItemDelegate):
    """Delegate for rendering basemap cards in a grid view."""

    tagBadgeClicked = pyqtSignal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_size = QSize(140, 100)
        self.text_height = 32
        self.text_height_hover = 60
        self.padding = 8
        self.border_radius = 6
        self.card_width = self.image_size.width()
        self.card_height = self.image_size.height() + self.text_height

    def _badge_rect(self, index, card_rect):
        tag = index.data(Qt.ItemDataRole.UserRole + 11)
        if not tag:
            return None
        display_tag = tag[tag.find("/") + 1 :] if "/" in tag else tag
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        fm = QFontMetrics(font)
        text_w = (
            fm.horizontalAdvance(display_tag)
            if hasattr(fm, "horizontalAdvance")
            else fm.boundingRect(display_tag).width()
        )
        badge_margin = 4
        badge_pad_h = 5
        badge_pad_v = 2
        badge_w = text_w + badge_pad_h * 2
        badge_h = fm.height() + badge_pad_v * 2
        return QRect(
            card_rect.right() - badge_w - badge_margin,
            card_rect.top() + badge_margin,
            badge_w,
            badge_h,
        )

    def _display_tag(self, index):
        tag = index.data(Qt.ItemDataRole.UserRole + 11)
        if not tag:
            return None
        # Translate the full tag (e.g. "Overlay/Hydrography" → "叠加层/水文")
        translated = QCoreApplication.translate("BasemapsDialog", tag)
        # Strip prefix after translation for compact badge display
        return translated[translated.find("/") + 1 :] if "/" in translated else translated

    def editorEvent(self, event, model, option, index):
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            rect = option.rect
            x_off = (rect.width() - self.card_width) // 2
            y_off = (rect.height() - self.card_height) // 2
            card_rect = QRect(
                rect.left() + x_off,
                rect.top() + y_off,
                self.card_width,
                self.card_height,
            )
            badge = self._badge_rect(index, card_rect)
            if badge and badge.contains(event.pos()):
                self.tagBadgeClicked.emit(index)
                return True
        return super().editorEvent(event, model, option, index)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = option.rect
        x_off = (rect.width() - self.card_width) // 2
        y_off = (rect.height() - self.card_height) // 2
        card_rect = QRect(
            rect.left() + x_off, rect.top() + y_off, self.card_width, self.card_height
        )

        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        bg_color = QColor(255, 255, 255)
        border_color = QColor(220, 230, 240)
        text_bg_color = QColor(245, 248, 255)

        if is_selected:
            border_color = QColor(74, 144, 226)
            text_bg_color = QColor(235, 242, 255)

        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(card_rect, self.border_radius, self.border_radius)

        img_rect = QRect(
            card_rect.left(), card_rect.top(), self.card_width, self.image_size.height()
        )

        path = QPainterPath()
        path.addRoundedRect(QRectF(card_rect), self.border_radius, self.border_radius)
        painter.setClipPath(path)

        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled_pix = pixmap.scaled(
                img_rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            c_x = (scaled_pix.width() - img_rect.width()) // 2
            c_y = (scaled_pix.height() - img_rect.height()) // 2
            painter.drawPixmap(
                img_rect,
                scaled_pix,
                QRect(c_x, c_y, img_rect.width(), img_rect.height()),
            )
        else:
            painter.fillRect(img_rect, QColor(250, 250, 250))
            icon = index.data(Qt.ItemDataRole.UserRole + 10)
            if isinstance(icon, QIcon):
                icon_rect = QRect(
                    img_rect.center().x() - 12, img_rect.center().y() - 12, 24, 24
                )
                icon.paint(painter, icon_rect)

        name = index.data(Qt.ItemDataRole.DisplayRole)

        painter.setPen(QColor(44, 62, 80))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)

        margin = 6
        normal_rect = QRect(
            card_rect.left() + margin,
            card_rect.bottom() - self.text_height + 2,
            self.card_width - 2 * margin,
            self.text_height - 4,
        )
        bound_rect = painter.boundingRect(
            normal_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, name
        )
        text_overflows = bound_rect.height() > normal_rect.height()

        should_expand = is_hovered and text_overflows
        current_text_height = (
            self.text_height_hover if should_expand else self.text_height
        )
        text_bg_rect = QRect(
            card_rect.left(),
            card_rect.bottom() - current_text_height,
            self.card_width,
            current_text_height,
        )

        if should_expand:
            painter.setClipping(False)
            hover_bg_color = QColor(text_bg_color)
            hover_bg_color.setAlpha(240)
            painter.fillRect(text_bg_rect, hover_bg_color)
        else:
            painter.setClipping(False)
            painter.setClipPath(path)
            painter.fillRect(text_bg_rect, text_bg_color)

        display_text_rect = text_bg_rect.adjusted(margin, 2, -margin, -2)
        painter.drawText(
            display_text_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, name
        )

        # Protocol badge (top-left corner, only for vector tiles)
        protocol = index.data(Qt.ItemDataRole.UserRole + 12)
        if protocol and protocol == "vector":
            painter.save()
            proto_color = PROTOCOL_COLORS.get(protocol, QColor(0, 0, 0))
            proto_label = QCoreApplication.translate("BasemapInputDialog", protocol.capitalize())
            proto_font = painter.font()
            proto_font.setPointSize(7)
            proto_font.setBold(True)
            painter.setFont(proto_font)
            proto_pad_h = 5
            proto_pad_v = 2
            proto_margin = 4
            proto_text_rect = painter.boundingRect(
                QRect(0, 0, 0, 0), Qt.AlignmentFlag.AlignLeft, proto_label
            )
            proto_w = proto_text_rect.width() + proto_pad_h * 2
            proto_h = proto_text_rect.height() + proto_pad_v * 2
            proto_rect = QRect(
                card_rect.left() + proto_margin,
                card_rect.top() + proto_margin,
                proto_w,
                proto_h,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(proto_color))
            painter.drawRoundedRect(proto_rect, 3, 3)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(proto_rect, Qt.AlignmentFlag.AlignCenter, proto_label)
            painter.restore()

        display_tag = self._display_tag(index)
        if display_tag:
            tag = index.data(Qt.ItemDataRole.UserRole + 11)
            painter.save()
            badge_color = TAG_COLORS.get(tag, QColor(150, 150, 150))
            badge_font = painter.font()
            badge_font.setPointSize(7)
            badge_font.setBold(True)
            painter.setFont(badge_font)
            badge_margin = 4
            badge_pad_h = 5
            badge_pad_v = 2
            text_rect = painter.boundingRect(
                QRect(0, 0, 0, 0), Qt.AlignmentFlag.AlignLeft, display_tag
            )
            badge_w = text_rect.width() + badge_pad_h * 2
            badge_h = text_rect.height() + badge_pad_v * 2
            badge_rect = QRect(
                card_rect.right() - badge_w - badge_margin,
                card_rect.top() + badge_margin,
                badge_w,
                badge_h,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(badge_color))
            painter.drawRoundedRect(badge_rect, 3, 3)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, display_tag)
            painter.restore()

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self.card_width + 20, self.card_height + 20)

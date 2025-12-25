from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QSize, QRect, QRectF, QPoint
from qgis.PyQt.QtGui import (
    QPainter, 
    QColor, 
    QFont, 
    QPen, 
    QBrush, 
    QPixmap, 
    QImage,
    QPainterPath,
    QIcon
)
from qgis.PyQt.QtWidgets import QStyledItemDelegate, QStyle

class BasemapCardDelegate(QStyledItemDelegate):
    """Delegate for rendering basemap cards in a grid view."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_size = QSize(140, 100)
        self.text_height = 32  # Optimized for exactly 2 lines of text
        self.text_height_hover = 60  # Expanded height on hover
        self.padding = 8
        self.border_radius = 6
        self.card_width = self.image_size.width()
        self.card_height = self.image_size.height() + self.text_height
        
    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = option.rect
        # Center the card in the item rect
        x_off = (rect.width() - self.card_width) // 2
        y_off = (rect.height() - self.card_height) // 2
        card_rect = QRect(rect.left() + x_off, rect.top() + y_off, self.card_width, self.card_height)

        is_selected = option.state & QStyle.State_Selected
        is_hovered = option.state & QStyle.State_MouseOver
        
        # Colors
        bg_color = QColor(255, 255, 255)
        border_color = QColor(220, 230, 240)
        text_bg_color = QColor(245, 248, 255)
        
        if is_selected:
            border_color = QColor(74, 144, 226)
            text_bg_color = QColor(235, 242, 255)

        # Draw card shadow/stroke
        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(card_rect, self.border_radius, self.border_radius)

        # Image area (Top portion)
        img_rect = QRect(card_rect.left(), card_rect.top(), self.card_width, self.image_size.height())
        
        # Clip image to rounded corners
        path = QPainterPath()
        path.addRoundedRect(QRectF(card_rect), self.border_radius, self.border_radius)
        painter.setClipPath(path)

        # Draw Preview Image
        pixmap = index.data(Qt.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            # Scale and center image
            scaled_pix = pixmap.scaled(img_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            # Center crop
            c_x = (scaled_pix.width() - img_rect.width()) // 2
            c_y = (scaled_pix.height() - img_rect.height()) // 2
            painter.drawPixmap(img_rect, scaled_pix, QRect(c_x, c_y, img_rect.width(), img_rect.height()))
        else:
            # Placeholder (blank or loading)
            painter.fillRect(img_rect, QColor(250, 250, 250))
            # Draw a subtle icon placeholder if it's the default icon
            icon = index.data(Qt.UserRole + 10) # Custom role for original icon
            if isinstance(icon, QIcon):
                icon_rect = QRect(img_rect.center().x() - 12, img_rect.center().y() - 12, 24, 24)
                icon.paint(painter, icon_rect)

        # Text area (Bottom portion) - check if expansion is needed
        name = index.data(Qt.DisplayRole)
        
        # Pre-calculate font and check text overflow
        painter.setPen(QColor(44, 62, 80))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        
        margin = 6
        normal_rect = QRect(card_rect.left() + margin, card_rect.bottom() - self.text_height + 2,
                           self.card_width - 2 * margin, self.text_height - 4)
        bound_rect = painter.boundingRect(normal_rect, Qt.AlignCenter | Qt.AlignVCenter | Qt.TextWordWrap, name)
        text_overflows = bound_rect.height() > normal_rect.height()
        
        # Expand on hover only if text overflows
        should_expand = is_hovered and text_overflows
        current_text_height = self.text_height_hover if should_expand else self.text_height
        text_bg_rect = QRect(card_rect.left(), card_rect.bottom() - current_text_height, self.card_width, current_text_height)
        
        # Draw background based on hover state
        if should_expand:
            # Draw semi-transparent background that extends over image
            painter.setClipping(False)
            hover_bg_color = QColor(text_bg_color)
            hover_bg_color.setAlpha(240)
            painter.fillRect(text_bg_rect, hover_bg_color)
        else:
            painter.setClipping(False)
            painter.setClipPath(path)
            painter.fillRect(text_bg_rect, text_bg_color)
        
        # Draw text
        display_text_rect = text_bg_rect.adjusted(margin, 2, -margin, -2)
        painter.drawText(display_text_rect, Qt.AlignCenter | Qt.AlignVCenter | Qt.TextWordWrap, name)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self.card_width + 20, self.card_height + 20)

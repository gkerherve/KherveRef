"""The Covers view: references as a grid of front pages, captioned with
title, authors and year — easier to recognise a paper by than by key."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from .model import ENTRY_TYPES
from .table_model import COL_TITLE

CARD_W, IMG_H, TEXT_H, PAD = 150, 200, 62, 10


class CoverDelegate(QStyledItemDelegate):
    def __init__(self, view, thumbnails, entry_at):
        super().__init__(view)
        self._thumbs = thumbnails
        self._entry_at = entry_at       # proxy index -> Entry

    def sizeHint(self, option, index):
        return QSize(CARD_W + 2 * PAD, IMG_H + TEXT_H + 2 * PAD)

    def paint(self, p: QPainter, option, index):
        e = self._entry_at(index)
        pal = option.palette
        r = option.rect.adjusted(PAD // 2, PAD // 2, -PAD // 2, -PAD // 2)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            bg = QColor(pal.highlight().color())
            bg.setAlphaF(0.22 if selected else 0.08)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(r), 10, 10)

        img = QRect(r.x() + (r.width() - CARD_W) // 2, r.y() + PAD // 2,
                    CARD_W, IMG_H)
        pm = self._thumbs.pixmap(e) if e is not None else None
        clip = QPainterPath()
        if pm is not None:
            scaled = pm.scaled(img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            target = QRect(img.x() + (img.width() - scaled.width()) // 2,
                           img.y(), scaled.width(), scaled.height())
            clip.addRoundedRect(QRectF(target), 6, 6)
            p.setClipPath(clip)
            p.drawPixmap(target, scaled)
            p.setClipping(False)
            p.setPen(QPen(QColor(0, 0, 0, 40), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(target).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        elif e is not None:
            # No PDF: a quiet card naming the reference type.
            card = QColor(pal.base().color())
            p.setPen(QPen(QColor(pal.mid().color()), 1))
            p.setBrush(card)
            p.drawRoundedRect(QRectF(img).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.setPen(pal.placeholderText().color())
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() * 0.85)
            p.setFont(f)
            p.drawText(img.adjusted(10, 12, -10, -10),
                       Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
                       ENTRY_TYPES.get(e.type, "Reference").upper())
            f.setPointSizeF(option.font.pointSizeF() * 1.05)
            f.setBold(True)
            p.setFont(f)
            p.setPen(pal.text().color())
            p.drawText(img.adjusted(12, 40, -12, -12),
                       Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
                       e.title[:120] or e.key)

        if e is not None:
            text = QRect(r.x() + 4, img.bottom() + 6, r.width() - 8, TEXT_H - 6)
            f = QFont(option.font)
            f.setBold(True)
            p.setFont(f)
            p.setPen(pal.text().color())
            fm = p.fontMetrics()
            title = fm.elidedText(e.title or e.key, Qt.ElideRight, text.width() * 2 - 20)
            p.drawText(QRect(text.x(), text.y(), text.width(), fm.height() * 2),
                       Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, title)
            f.setBold(False)
            f.setPointSizeF(option.font.pointSizeF() * 0.9)
            p.setFont(f)
            p.setPen(pal.placeholderText().color())
            meta = " · ".join(x for x in (e.author_text(), e.year) if x)
            sub = p.fontMetrics().elidedText(meta, Qt.ElideRight, text.width())
            p.drawText(QRect(text.x(), text.y() + fm.height() * 2 + 2,
                             text.width(), p.fontMetrics().height()),
                       Qt.AlignHCenter, sub)
            if e.needs_review:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#f2b01e"))
                p.drawEllipse(img.topRight().x() - 14, img.y() + 6, 9, 9)
        p.restore()


class CoversView(QListView):
    def __init__(self, thumbnails, entry_at, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setWordWrap(True)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Select whole references, as the list does: the details pane,
        # Delete and the menus all act on selected rows.
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setItemDelegate(CoverDelegate(self, thumbnails, entry_at))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    def setModel(self, model):
        super().setModel(model)
        self.setModelColumn(COL_TITLE)

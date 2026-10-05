# Copyright (C) 2026 Gwilherm Kerherve
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""The KherveRef application mark (window / taskbar icon).

A "Kref" wordmark above an open book with a bookmark ribbon on a
rounded teal tile, in the shared Kherve-family style (cf. KhervePDF's
appmark): the symbol is a stroked vector path, so it stays crisp from
16 px to 256 px.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPainter,
                           QPainterPath, QPen, QPixmap, QTransform)

_FILL = "#00796b"
_EDGE = "#005f56"
_INK = "#ffffff"


def _stroke(p, path, color, box, weight):
    t = QTransform()
    t.translate(box.x(), box.y())
    t.scale(box.width(), box.height())
    pen = QPen(QColor(color))
    pen.setWidthF(weight * box.height())
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(t.map(path))


def _tile(p, s):
    m = s * 0.06
    radius = s * 0.22
    rect = QRectF(m, m, s - 2 * m, s - 2 * m)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(_FILL))
    p.drawRoundedRect(rect, radius, radius)
    pen = QPen(QColor(_EDGE))
    pen.setWidthF(max(1.0, s * 0.02))
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect, radius, radius)
    return rect


def _wordmark(p, rect, text):
    """*text* centred in *rect*, at the largest bold size that fits."""
    avail = rect.width() * 0.82
    font = QFont("Segoe UI")
    font.setBold(True)
    size = 1.0
    while size < rect.height():
        font.setPointSizeF(size + 0.5)
        fm = QFontMetricsF(font)
        if fm.horizontalAdvance(text) > avail or fm.height() > rect.height():
            break
        size += 0.5
    font.setPointSizeF(size)
    p.setFont(font)
    p.setPen(QColor(_INK))
    p.drawText(rect, Qt.AlignCenter, text)


def _open_book(p, box):
    """Two facing pages meeting at the spine, with a bookmark ribbon."""
    book = QPainterPath()
    book.moveTo(0.50, 0.20)
    book.cubicTo(0.38, 0.08, 0.16, 0.08, 0.02, 0.14)
    book.lineTo(0.02, 0.86)
    book.cubicTo(0.16, 0.80, 0.38, 0.80, 0.50, 0.92)
    book.cubicTo(0.62, 0.80, 0.84, 0.80, 0.98, 0.86)
    book.lineTo(0.98, 0.14)
    book.cubicTo(0.84, 0.08, 0.62, 0.08, 0.50, 0.20)
    book.lineTo(0.50, 0.92)
    _stroke(p, book, _INK, box, 0.06)
    ribbon = QPainterPath()
    ribbon.moveTo(0.70, 0.12); ribbon.lineTo(0.70, 0.58)
    ribbon.lineTo(0.77, 0.50); ribbon.lineTo(0.84, 0.58)
    ribbon.lineTo(0.84, 0.10)
    _stroke(p, ribbon, _INK, box, 0.05)


def paint(size: int) -> QPixmap:
    """Render the Kref mark at *size* px."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rect = _tile(p, float(size))
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _wordmark(p, QRectF(x, y + h * 0.05, w, h * 0.44), "Kref")
    bw, bh = w * 0.56, h * 0.36
    _open_book(p, QRectF(x + (w - bw) / 2.0, y + h * 0.54, bw, bh))
    p.end()
    return pm

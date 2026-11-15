"""Draw the app icon (radio waves on a blue tile): data/icon.png and packaging/icon.ico.

Run: python packaging/make_icon.py
"""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

HERE = Path(__file__).resolve().parent
PNG = HERE.parent / "src" / "bluebench" / "data" / "icon.png"
ICO = HERE / "icon.ico"


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0b5cad"))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    origin = QPointF(70 * s, 186 * s)
    p.setBrush(QColor("white"))
    p.drawEllipse(origin, 20 * s, 20 * s)
    pen = QPen(QColor("white"), 20 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for r in (50, 90, 130):
        rect = QRectF(origin.x() - r * s, origin.y() - r * s, 2 * r * s, 2 * r * s)
        p.drawArc(rect, 0 * 16, 90 * 16)
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    draw(256).save(str(PNG))
    # Qt's ICO writer stores one size per file; 256 px scales well in Explorer.
    if not draw(256).save(str(ICO)):
        raise SystemExit("Could not write icon.ico (Qt imageformats plugin missing?)")


if __name__ == "__main__":
    main()

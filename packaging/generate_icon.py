"""Render the app mark (kherveref/appmark.py) into the files installers
need: build/KherveRef.ico (Windows) and build/KherveRef.png (1024 px,
which PyInstaller turns into the macOS .icns via Pillow)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from kherveref import appmark

    QApplication.instance() or QApplication([])
    out = ROOT / "build"
    out.mkdir(exist_ok=True)

    def pil(size: int) -> "Image.Image":
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        appmark.paint(size).save(buf, "PNG")
        from io import BytesIO
        return Image.open(BytesIO(bytes(buf.data()))).convert("RGBA")

    big = pil(1024)
    big.save(out / "KherveRef.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pil(256).save(out / "KherveRef.ico", sizes=[(s, s) for s in sizes])
    print(f"wrote {out / 'KherveRef.ico'} and {out / 'KherveRef.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

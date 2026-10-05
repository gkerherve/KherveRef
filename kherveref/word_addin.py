"""Register (or remove) the KherveRef panel with Microsoft Word.

The panel's manifest (word_manifest.xml, the same file published in
docs/word/) tells Word where the panel lives. Word picks it up from:

* macOS: ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/
* Windows: HKCU\\Software\\Microsoft\\Office\\16.0\\WEF\\Developer,
  a value naming the manifest file (Word's developer add-ins).

Word then shows it under Insert ▸ Add-ins ▸ My Add-ins (Developer
Add-ins) and as a "Cite" button on the Home tab, after a restart.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import state

MANIFEST = Path(__file__).resolve().parent / "word_manifest.xml"
ADDIN_ID = "8f4c2a61-3b7e-4d2a-9c55-6e1b0a7d4f20"
FILENAME = "kherveref-manifest.xml"
_REG_KEY = r"Software\Microsoft\Office\16.0\WEF\Developer"


def mac_wef_dir() -> Path:
    return (Path.home() / "Library" / "Containers" / "com.microsoft.Word"
            / "Data" / "Documents" / "wef")


def is_installed() -> bool:
    if sys.platform == "darwin":
        return (mac_wef_dir() / FILENAME).exists()
    if sys.platform.startswith("win"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
                winreg.QueryValueEx(k, ADDIN_ID)
                return True
        except OSError:
            return False
    return False


def install() -> str:
    """Register the panel; returns where. Raises OSError on failure."""
    if sys.platform == "darwin":
        d = mac_wef_dir()
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MANIFEST, d / FILENAME)
        return str(d / FILENAME)
    if sys.platform.startswith("win"):
        import winreg
        target = state.state_dir() / "word" / FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MANIFEST, target)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            winreg.SetValueEx(k, ADDIN_ID, 0, winreg.REG_SZ, str(target))
        return str(target)
    raise OSError("Microsoft Word add-ins are only available on Windows and macOS")


def uninstall() -> None:
    if sys.platform == "darwin":
        (mac_wef_dir() / FILENAME).unlink(missing_ok=True)
    elif sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, ADDIN_ID)
        except OSError:
            pass
        (state.state_dir() / "word" / FILENAME).unlink(missing_ok=True)

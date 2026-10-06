"""Register (or remove) the KherveRef panel with Microsoft Word.

The panel's manifest (word_manifest.xml, the same file published in
docs/word/) tells Word where the panel lives. Word picks it up from:

* macOS: ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/
  — Word's own container, which macOS does not let other apps write to
  (not even Finder when scripted). KherveRef then has Finder create the
  folder, prepares the file in ~/Documents/KherveRef and opens both, for
  the user to drag the file across once: a copy the user makes in
  Finder is allowed.
* Windows: HKCU\\Software\\Microsoft\\Office\\16.0\\WEF\\Developer,
  a value naming the manifest file (Word's developer add-ins).

Word then shows it under Insert ▸ Add-ins ▸ My Add-ins (Developer
Add-ins) and as a "Cite" button on the Home tab, after a restart.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import state

MANIFEST = Path(__file__).resolve().parent / "word_manifest.xml"
ADDIN_ID = "8f4c2a61-3b7e-4d2a-9c55-6e1b0a7d4f20"
FILENAME = "kherveref-manifest.xml"
_REG_KEY = r"Software\Microsoft\Office\16.0\WEF\Developer"

# Word hides developer add-ins three clicks deep and often shows no ribbon
# button for them, so every place that mentions the panel spells this out.
OPEN_STEPS = (
    "To open the KherveRef panel in Word:\n\n"
    "1. Quit Word completely (⌘Q on a Mac) and open it again.\n"
    "2. On the Home tab, click Add-ins (at the far right of the ribbon).\n"
    "3. Click More Add-ins.\n"
    "4. At the top of that window, click the My Add-ins tab.\n"
    "5. Under Developer Add-ins, click KherveRef (then Add, if Word asks).\n\n"
    "The panel opens on the right of your document. Word may also add a "
    "Cite button to the Home tab, but not always: Add-ins ▸ More Add-ins ▸ "
    "My Add-ins ▸ KherveRef always works.\n\n"
    "Keep KherveRef open while you cite: the panel comes from it.")


def mac_wef_dir() -> Path:
    return (Path.home() / "Library" / "Containers" / "com.microsoft.Word"
            / "Data" / "Documents" / "wef")


def is_installed() -> bool:
    if sys.platform == "darwin":
        try:
            return (mac_wef_dir() / FILENAME).exists()
        except PermissionError:
            return False
    if sys.platform.startswith("win"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
                winreg.QueryValueEx(k, ADDIN_ID)
                return True
        except OSError:
            return False
    return False


@dataclass
class InstallResult:
    done: bool              # False: the user still has one drag to make
    manifest: Path          # where the manifest is (or waits to be dragged from)
    folder: Path | None = None   # macOS: the wef folder to drag it into


def staging_dir() -> Path:
    return Path.home() / "Documents" / "KherveRef"


def install() -> InstallResult:
    """Register the panel. Raises OSError when it cannot even prepare."""
    if sys.platform == "darwin":
        d = mac_wef_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(MANIFEST, d / FILENAME)
            return InstallResult(True, d / FILENAME)
        except PermissionError:
            pass
        # Finder may create the folder for us; the copy itself it may not.
        subprocess.run(["osascript", "-e",
                        'set d to (POSIX file "' + str(d.parent) + '") as alias\n'
                        'tell application "Finder" to if not (exists folder '
                        '"wef" of d) then make new folder at d with properties '
                        '{name:"wef"}'], capture_output=True, timeout=30)
        staged = staging_dir() / FILENAME
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MANIFEST, staged)
        subprocess.run(["open", str(d)], capture_output=True)
        subprocess.run(["open", "-R", str(staged)], capture_output=True)
        return InstallResult(False, staged, d)
    if sys.platform.startswith("win"):
        import winreg
        target = state.state_dir() / "word" / FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MANIFEST, target)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            winreg.SetValueEx(k, ADDIN_ID, 0, winreg.REG_SZ, str(target))
        return InstallResult(True, target)
    raise OSError("Microsoft Word add-ins are only available on Windows and macOS")


def uninstall() -> None:
    if sys.platform == "darwin":
        try:
            (mac_wef_dir() / FILENAME).unlink(missing_ok=True)
        except PermissionError:
            raise OSError("macOS doesn't let KherveRef change Word's add-in "
                          f"folder. Delete {FILENAME} from it in Finder "
                          "(Word ▸ Install… opens the folder).") from None
    elif sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, ADDIN_ID)
        except OSError:
            pass
        (state.state_dir() / "word" / FILENAME).unlink(missing_ok=True)

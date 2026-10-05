"""Shared test setup.

Tests must never touch the user's real preferences (recent libraries,
theme). On macOS ``QSettings(org, app)`` always uses the native plist
store whatever ``setDefaultFormat`` says, so QSettings is replaced —
before any kherveref module imports it — by a subclass that maps the
``(org, app)`` form onto a throwaway INI file.
"""
import atexit
import os
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore

_REAL = QtCore.QSettings
_ROOT = tempfile.mkdtemp(prefix="kherveref-test-settings-")
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)


class _IsolatedSettings(_REAL):
    def __init__(self, *args):
        if len(args) == 2 and all(isinstance(a, str) for a in args):
            org, app = args
            super().__init__(os.path.join(_ROOT, f"{org}.{app}.ini"),
                             _REAL.IniFormat)
        else:
            super().__init__(*args)


QtCore.QSettings = _IsolatedSettings

# (Qt reports "/" separators even on Windows, so compare normalised paths.)
assert os.path.normcase(os.path.dirname(os.path.normpath(
    QtCore.QSettings("kherve", "KherveRef").fileName()))) == \
    os.path.normcase(os.path.normpath(_ROOT))

# The same for the Qt-free state file (recent libraries for the MCP server).
from pathlib import Path  # noqa: E402

from kherveref import state  # noqa: E402

state.state_dir = lambda: Path(_ROOT) / "state"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

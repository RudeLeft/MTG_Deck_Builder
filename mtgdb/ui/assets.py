"""Bundled-asset location and PIL availability shared by UI modules.

Assets live in the repository-root ``assets/`` directory when running from
source, and under the PyInstaller ``_MEIPASS`` tree when frozen. Both the app
shell and the window-services mixin resolve bundled files through here so the
lookup rule has a single owner. Mana rendering also consumes the exported PIL
handles from this module rather than probing Pillow independently.
"""

import os
import sys

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except Exception:
    Image = None
    ImageTk = None
    HAVE_PIL = False

APP_ICON_FILE = "magic_icon.ico"


def _asset_path(name):
    """Locate a bundled asset, whether running from source or a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)  # set when frozen
    if base:
        return os.path.join(base, "assets", name)
    # From source: assets/ sits beside the mtgdb package (repository root).
    package_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(package_root, "assets", name)

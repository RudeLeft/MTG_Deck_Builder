# -*- mode: python ; coding: utf-8 -*-
#
# Portable ONEDIR build for MTG Deck Builder.
#
# IMPORTANT:
# This intentionally does NOT build a one-file executable. PyInstaller one-file
# apps extract bundled files into Windows Temp at runtime. ONEDIR keeps Python,
# Tkinter, Pillow, and assets inside dist\MTGDeckBuilder\ so runtime files stay
# within the application folder.
#
# Build:
#     pyinstaller MTGDeckBuilder.spec --noconfirm
#
# Result:
#     dist\MTGDeckBuilder\MTGDeckBuilder.exe
#     dist\MTGDeckBuilder\_internal\...
#
# UPX is deliberately disabled. It was previously requested but never installed,
# so it silently did nothing; and UPX-compressed Python/Tk executables are a
# well-known antivirus false-positive trigger for very little size saving.
#
# The application itself creates only:
#     dist\MTGDeckBuilder\data\...

# Version metadata is read from pyproject.toml so the shipped .exe reports the
# same version as the project manifest and can never drift from it.  Without
# this the file's Details tab is blank, which makes a bug report impossible to
# tie to a build.
import tomllib
from pathlib import Path as _Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

_project = tomllib.loads(
    (_Path(SPECPATH) / "pyproject.toml").read_text(encoding="utf-8"))["project"]
_version = str(_project["version"])
_parts = tuple(int(part) for part in _version.split(".")[:3]) + (0,) * 4
_filevers = _parts[:4]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_filevers, prodvers=_filevers,
                      mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                      subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "MTG Deck Builder"),
            StringStruct("FileDescription", str(_project["description"])),
            StringStruct("FileVersion", _version),
            StringStruct("InternalName", "MTGDeckBuilder"),
            StringStruct("OriginalFilename", "MTGDeckBuilder.exe"),
            StringStruct("ProductName", "MTG Deck Builder"),
            StringStruct("ProductVersion", _version),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)


a = Analysis(
    ['mtgdb/main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['reportlab.pdfgen.canvas', 'reportlab.lib.pagesizes', 'reportlab.lib.utils'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MTGDeckBuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/magic_icon.ico',
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MTGDeckBuilder',
)

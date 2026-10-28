# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the DeepSeek Autonomous Code Agent release.

Packages the updated Code Agent GUI with configurable live request timeout,
non-blocking unattended error reporting, bundled architecture specifications,
Space Fractions game components, and offline mock/synthesis pipeline.
Strictly excludes .env, credentials, test directories, and packaging virtual environments.
Target entry point: src/agent_gui.py
Output executable: dist/CodeAgent.exe
"""

import os
import sys
from PyInstaller.building.datastruct import TOC

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
# The Python 3.12 Tcl/Tk 8.6.15 runtime on this host cannot initialize an
# interpreter even when its matching library scripts are present.  The
# installed MinGW Tcl/Tk 8.6.16 runtime is verified with _tkinter in an
# isolated process and is packaged under the DLL names _tkinter imports.
TK_RUNTIME_ROOT = os.environ.get("CODE_AGENT_TK_RUNTIME_ROOT", r"C:\msys64\mingw64")
TK_RUNTIME_BIN = os.path.join(TK_RUNTIME_ROOT, "bin")
TK_RUNTIME_LIB = os.path.join(TK_RUNTIME_ROOT, "lib")


def _require_runtime_path(*parts):
    path = os.path.join(*parts)
    if not os.path.exists(path):
        raise SystemExit(f"Missing verified Tcl/Tk runtime resource: {path}")
    return path

entry_point = os.path.join(PROJECT_ROOT, "src", "agent_gui.py")

a = Analysis(
    [entry_point],
    pathex=[PROJECT_ROOT],
    binaries=[
        (os.path.join(sys.base_prefix, "DLLs", "_tkinter.pyd"), "."),
    ],
    datas=[
        (os.path.join(PROJECT_ROOT, "Architecture_Documentation.md"), "."),
        (os.path.join(PROJECT_ROOT, "Architecture_View.md"), "."),
        (os.path.join(PROJECT_ROOT, "space_fractions"), "space_fractions"),
        (os.path.join(PROJECT_ROOT, "specs"), "specs"),
        (_require_runtime_path(TK_RUNTIME_LIB, "tcl8.6"), "_tcl_data"),
        (_require_runtime_path(TK_RUNTIME_LIB, "tk8.6"), "_tk_data"),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "src.spec_preprocessor",
        "src.generator_pipeline",
        "src.budget_controller",
        "src.path_security",
        "src.command_validator",
        "src.agent_loop",
        "src.response_parser",
        "src.synthesis_driver",
        "src.project_validator",
        "src.deepseek_client",
        "space_fractions",
        "space_fractions.main",
        "space_fractions.game_engine",
        "space_fractions.question_bank",
        "space_fractions.fraction_math",
        "space_fractions.ui_screens",
    ],
    hookspath=[os.path.join(SPECPATH, "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "pytest",
        "packaging_poc",
        "_pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# PyInstaller first collects _tkinter's host dependencies.  Remove its broken
# Python 8.6.15 Tcl/Tk entries before adding the verified 8.6.16 runtime.
a.binaries = TOC([
    entry for entry in a.binaries
    if entry[0].lower() not in {"tcl86t.dll", "tk86t.dll", "zlib1.dll"}
])

# _tkinter imports the Tcl/Tk DLLs by their python.org names.  The verified
# 8.6.16 runtime uses un-suffixed filenames, so retain its source files while
# giving the bundled copies the import names required by _tkinter.
a.binaries += TOC([
    ("tcl86t.dll", _require_runtime_path(TK_RUNTIME_BIN, "tcl86.dll"), "BINARY"),
    ("tk86t.dll", _require_runtime_path(TK_RUNTIME_BIN, "tk86.dll"), "BINARY"),
    ("zlib1.dll", _require_runtime_path(TK_RUNTIME_BIN, "zlib1.dll"), "BINARY"),
])

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CodeAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windowed desktop application (IMAGE_SUBSYSTEM_WINDOWS_GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

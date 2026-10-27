# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Space Fractions Educational Game — OneFile Windows Build.

Standalone desktop packaging of the Grade 6 Space Fractions math game.
Strictly excludes .env, API credentials, Code Agent source, and test directories.
Target entry point: space_fractions_stage5_integration/space_fractions/main.py
"""

import os

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

entry_point = os.path.join(
    PROJECT_ROOT, "space_fractions_stage5_integration", "space_fractions", "main.py"
)

a = Analysis(
    [entry_point],
    pathex=[
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, "space_fractions_stage5_integration"),
    ],
    binaries=[],
    datas=[
        (os.path.join(PROJECT_ROOT, "space_fractions"), "space_fractions"),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "space_fractions",
        "space_fractions.fraction_math",
        "space_fractions.game_engine",
        "space_fractions.question_bank",
        "space_fractions.ui_screens",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "src",
        "tests",
        "pytest",
        "_pytest",
        "packaging_poc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SpaceFractions",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windowed desktop application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

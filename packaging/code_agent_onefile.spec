# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DeepSeek Autonomous Code Agent — OneFile Development Build.

Offline packaging with bundled Space Fractions game assets and architecture specifications.
Strictly excludes .env, API credentials, and test directories.
"""

import os

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(PROJECT_ROOT, "src", "agent_gui.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[
        (os.path.join(PROJECT_ROOT, "Architecture_Documentation.md"), "."),
        (os.path.join(PROJECT_ROOT, "Architecture_View.md"), "."),
        (os.path.join(PROJECT_ROOT, "space_fractions"), "space_fractions"),
        (os.path.join(PROJECT_ROOT, "specs"), "specs"),
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
    ],
    hookspath=[],
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CodeAgent_Offline",
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

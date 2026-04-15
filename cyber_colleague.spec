# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Windows (build on Windows). Bundles default skill_lib/zhang_jing as zhang_jing.
# Includes OpenSSL DLLs required by stdlib `_ssl` (fixes ImportError when using HTTPS / openai).

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# In PyInstaller spec, SPEC is the path to this file.
project_root = Path(SPEC).resolve().parent


def _pyside6_runtime_bundle() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Explicitly ship Qt plugins (e.g. platforms/qwindows.dll) and Qt/PySide6 DLLs.

    On some Windows setups the PySide6 hook cannot query Qt paths during build
    (child process fails to import QtCore), so auto-collection is incomplete and
    users see: "no Qt platform plugin could be initialized".
    """
    datas = collect_data_files("PySide6", includes=["**/plugins/**"])
    binaries = collect_dynamic_libs("PySide6")
    return datas, binaries


def _windows_openssl_binaries() -> list[tuple[str, str]]:
    """Ship libssl/libcrypto next to the frozen app (PyInstaller often misses these on Conda/venv)."""
    if sys.platform != "win32":
        return []
    base = Path(getattr(sys, "base_prefix", sys.prefix)).resolve()
    search_dirs = [
        base,
        base / "DLLs",
        base / "Library" / "bin",
        Path(sys.executable).resolve().parent,
    ]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in search_dirs:
        if not d.is_dir():
            continue
        for pattern in ("libssl*.dll", "libcrypto*.dll"):
            for p in sorted(d.glob(pattern)):
                if not p.is_file():
                    continue
                key = p.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((str(p), "."))
    if not out:
        raise RuntimeError(
            "未找到 libssl/libcrypto DLL（请使用 Python 3.10+，并确认安装目录下存在 "
            "Library\\bin 或 DLLs 中的 OpenSSL DLL；推荐使用 python.org 官方安装包）。"
        )
    return out


block_cipher = None

_pyside_datas, _pyside_bins = _pyside6_runtime_bundle()

datas = [
    (str(project_root / "skill_lib" / "zhang_jing"), "zhang_jing"),
    (str(project_root / "pic"), "pic"),
] + _pyside_datas

# 排除本应用未使用的大型/可选包，缩短依赖分析时间（不改变业务代码）。
_EXCLUDE_UNUSED = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "IPython",
    "jupyter",
    "pytest",
    "PyQt5",
    "PyQt6",
    "PySide2",
]

a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=_windows_openssl_binaries() + _pyside_bins,
    datas=datas,
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "openai", "_ssl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDE_UNUSED,
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
    name="CyberColleague",
    icon=str(project_root / "pic" / "soft_icon" / "soft.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 关闭 UPX 可明显缩短打包耗时（Qt/PySide 体量大，压缩很慢）；exe 体积会变大，加载略快。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

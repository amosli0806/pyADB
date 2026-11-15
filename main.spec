# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        ('tools', 'tools'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'pandas', 'numpy',
        'scipy', 'IPython', 'jupyter', 'test', 'distutils',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='捕虫师',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                     # 禁用 UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # 隐藏控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/app_icon.ico'
)

# ========== 关键新增：COLLECT 生成 onedir 目录 ==========
coll = COLLECT(
    exe,                           # 包含上述生成的 exe
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='捕虫师'              # 输出目录名，位于 dist/BugCatcher/
)
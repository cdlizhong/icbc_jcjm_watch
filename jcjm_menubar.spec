# -*- mode: python ; coding: utf-8 -*-
# 工行积存金行情 — macOS 菜单栏应用打包配置
#
# 构建：
#   .venv/bin/pyinstaller jcjm_menubar.spec --noconfirm
# 产物：
#   dist/积存金行情.app   （onedir；双击即用，ad-hoc 签名，未做开发者签名/公证）
#
# LSUIElement=1：仅出现在顶部菜单栏，不显示 Dock 图标。

# pyobjc 模块通过 .so/.dylib 加载，PyInstaller 无内置 hook，需手动列出全部子模块
_PYOBJC_HIDDEN = [
    'objc', 'objc._objc', 'objc._dyld', 'objc._framework', 'objc._lazyimport',
    'objc._convenience', 'objc._convenience_mapping', 'objc._convenience_nsarray',
    'objc._convenience_nsdata', 'objc._convenience_nsdecimal',
    'objc._convenience_nsdictionary', 'objc._convenience_nsobject',
    'objc._convenience_nsset', 'objc._convenience_nsstring',
    'objc._convenience_sequence', 'objc._descriptors', 'objc._bridges',
    'objc._bridgesupport', 'objc._callable_docstr', 'objc._category',
    'objc._compat', 'objc._context', 'objc._informal_protocol',
    'objc._locking', 'objc._machsignals', 'objc._new', 'objc._properties',
    'objc._protocols', 'objc._pycoder', 'objc._pythonify', 'objc._structtype',
    'objc._transform', 'objc.simd',
    'CoreFoundation', 'CoreFoundation._CoreFoundation', 'CoreFoundation._metadata',
    'CoreFoundation._inlines', 'CoreFoundation._static',
    'Foundation', 'Foundation._Foundation', 'Foundation._context',
    'Foundation._functiondefines', 'Foundation._inlines',
    'Foundation._nsindexset', 'Foundation._nsobject', 'Foundation._nsurl',
    'AppKit', 'AppKit._AppKit', 'AppKit._inlines', 'AppKit._nsapp',
    'Cocoa',
    'PyObjCTools', 'PyObjCTools.AppHelper', 'PyObjCTools.AppCategories',
    'PyObjCTools.Conversion', 'PyObjCTools.FndCategories',
    'PyObjCTools.KeyValueCoding', 'PyObjCTools.MachSignals',
    'PyObjCTools.Signals', 'PyObjCTools.TestSupport',
    'rumps', 'rumps._internal', 'rumps.compat', 'rumps.events',
    'rumps.exceptions', 'rumps.notifications', 'rumps.rumps',
    'rumps.text_field', 'rumps.utils',
]

a = Analysis(
    ['menubar_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_PYOBJC_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 菜单栏应用用不到 Flask 网页服务与其它重型库，排除以缩小体积
    excludes=['flask', 'tkinter', 'matplotlib', 'PIL', 'pytest', 'numpy'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JCJMMenuBar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI 应用（.app）
    codesign_identity='-',   # ad-hoc 签名，本机可直接运行
    entitlements_file='entitlements.plist',  # 允许加载 ad-hoc 重签的 Xcode Python 框架
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='JCJMMenuBar',
)

app = BUNDLE(
    coll,
    name='积存金行情.app',
    icon=None,
    bundle_identifier='com.local.icbc-jcjm-watch',
    info_plist={
        'CFBundleName': '积存金行情',
        'CFBundleDisplayName': '积存金行情',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': '1',          # 代理应用（agent）：无 Dock 图标
        'NSHumanReadableCopyright': '数据来自工行公开行情页，仅供查阅，以银行柜台为准',
    },
)

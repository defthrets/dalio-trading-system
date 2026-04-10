# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off', 'api', 'api.server', 'api.state', 'api.brokers', 'api.scanners', 'api.signals', 'api.portfolio', 'api.websocket', 'api.auth', 'api.utils', 'api.agent', 'agents', 'agents.dalio_agent', 'data', 'data.storage', 'data.storage.models', 'config', 'config.settings', 'config.assets', 'notifications', 'engines', 'trading', 'backtesting', 'sqlalchemy.dialects.sqlite', 'multiprocessing']
hiddenimports += collect_submodules('webview')
hiddenimports += collect_submodules('uvicorn')


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui'), ('config', 'config'), ('api', 'api'), ('agents', 'agents'), ('data', 'data'), ('notifications', 'notifications'), ('engines', 'engines'), ('trading', 'trading'), ('backtesting', 'backtesting')],
    hiddenimports=hiddenimports,
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
    name='DALIOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ui\\static\\favicon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DALIOS',
)

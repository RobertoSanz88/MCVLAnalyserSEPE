# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\run.py'],
    pathex=[],
    binaries=[],
    # Plantillas Jinja2 e iconos/imagenes estaticas (app/), y SOLO el CSV de
    # claves sucesoras de tipo de contrato (etl/metadata, usado en tiempo de
    # import por etl/tipo_contrato_claves_sucesoras.py, que a su vez importa
    # app/routers/trayectorias.py) - ninguno se detecta solo, PyInstaller solo
    # sigue imports de codigo Python, no ficheros de datos sueltos.
    # OJO: no meter aqui toda la carpeta etl/metadata/ - provincias_dict.csv y
    # cnae_3d_dict.csv son solo para etl/webui (construccion del Parquet, uso
    # restringido, ver CLAUDE.md), la app de analisis no los necesita.
    datas=[
        ('..\\app\\templates', 'app\\templates'),
        ('..\\app\\static', 'app\\static'),
        ('..\\etl\\metadata\\tipo_contrato_claves_sucesoras.csv', 'etl\\metadata'),
    ],
    hiddenimports=['uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'uvicorn.logging'],
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
    name='mcvlanalyser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='mcvlanalyser',
)

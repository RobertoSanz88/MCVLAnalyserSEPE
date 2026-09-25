# -*- mode: python ; coding: utf-8 -*-

# Dos ejecutables en el mismo onedir (comparten _internal): "mcvlanalyser"
# (la app real, FastAPI+uvicorn+DuckDB) y "splash" (ventana ligera con la
# imagen de arranque, solo tkinter/urllib/config - nada de pandas/duckdb/
# fastapi). Separados a proposito: mcvlanalyser.py lanza splash.exe como
# proceso aparte ANTES de importar app.main (ver run.py) para que la imagen
# aparezca mientras cargan las dependencias pesadas, no despues - si el
# splash compartiera el mismo proceso/exe que la app, tendria que esperar a
# las mismas importaciones lentas antes de poder mostrarse siquiera.

a_main = Analysis(
    ['..\\run.py'],
    pathex=[],
    binaries=[],
    # Plantillas Jinja2 e iconos/imagenes estaticas (app/), y los CSV de
    # etl/metadata que la app de analisis lee en tiempo de ejecucion -
    # ninguno se detecta solo, PyInstaller solo sigue imports de codigo
    # Python, no ficheros de datos sueltos:
    #   - tipo_contrato_claves_sucesoras.csv: import de
    #     etl/tipo_contrato_claves_sucesoras.py, que a su vez importa
    #     app/routers/trayectorias.py.
    #   - provincias_dict.csv: leido por app/routers/trayectorias.py para
    #     decodificar la columna Provincia de "Trayectorias individuales".
    # cnae_3d_dict.csv SI sigue siendo solo para etl/webui (construccion del
    # Parquet, uso restringido, ver CLAUDE.md) - la app de analisis muestra
    # CNAE con su codigo crudo, sin decodificar, no lo necesita.
    datas=[
        ('..\\app\\templates', 'app\\templates'),
        ('..\\app\\static', 'app\\static'),
        # README.md: la vista "Acerca de" (v2.2) lo lee de la raiz del paquete (app/routers/muestras.py, _ruta_recurso).
        ('..\\README.md', '.'),
        ('..\\etl\\metadata\\tipo_contrato_claves_sucesoras.csv', 'etl\\metadata'),
        ('..\\etl\\metadata\\provincias_dict.csv', 'etl\\metadata'),
    ],
    hiddenimports=['uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'uvicorn.logging'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_main = PYZ(a_main.pure)

exe_main = EXE(
    pyz_main,
    a_main.scripts,
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

# splash.py solo necesita su propia imagen (app/static/splash.png) - el resto
# de app/static/app/templates ya los trae exe_main, y COLLECT los junta en el
# mismo _internal (mismo destino relativo "app\\static", sin duplicar disco).
a_splash = Analysis(
    ['..\\splash.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('..\\app\\static\\splash.png', 'app\\static'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_splash = PYZ(a_splash.pure)

exe_splash = EXE(
    pyz_splash,
    a_splash.scripts,
    [],
    exclude_binaries=True,
    name='splash',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # ventana sin consola, igual que "pythonw splash.py" en dev
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_main,
    a_main.binaries,
    a_main.datas,
    exe_splash,
    a_splash.binaries,
    a_splash.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='mcvlanalyser',
)

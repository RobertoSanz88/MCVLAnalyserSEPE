@echo off
REM Lanzador de la app de analisis MCVL (FastAPI + DuckDB). Mismo patron que el
REM lanzador de etl/webui/iniciar.bat: instala dependencias si faltan y arranca
REM el servidor. Doble clic desde cualquier sitio (usa rutas relativas a este .bat).
REM
REM Sirve tanto para el PC personal como para el corporativo con Netskope: prueba la
REM instalacion normal primero, y solo si falla (tipico con el proxy SSL de Netskope)
REM reintenta apuntando a los hosts de confianza.
REM
REM Nota: escrito con goto en vez de bloques if (...) anidados a proposito - cmd.exe
REM cuenta parentesis para saber donde termina un bloque if (...), y basta un parentesis
REM suelto dentro del texto de un echo para que el parser se confunda y falle con
REM "No se esperaba ... en este momento". goto evita el problema por completo.

setlocal
cd /d "%~dp0"

echo Comprobando dependencias de requirements.txt...
python -m pip install -r requirements.txt --quiet
if not errorlevel 1 goto :arrancar

echo Instalacion normal fallida, reintentando con host de confianza para PC corporativo...
python -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org --quiet
if not errorlevel 1 goto :arrancar

echo.
echo No se han podido instalar las dependencias. Si estas en el PC corporativo,
echo puede hacer falta el certificado interno - ver CLAUDE.md.
pause
exit /b 1

:arrancar
REM Puerto REAL en el que va a arrancar run.py (no solo el preferido): si el
REM preferido (normalmente 8000, salvo que config.json diga otro) esta
REM ocupado de forma permanente por otro programa (visto en el PC
REM corporativo del usuario), puerto_libre busca el primero libre a partir de
REM ahi - mismo criterio que aplica run.py (ver comentario alli, 2026-08-28).
REM Se resuelve aqui y se pasa por variable de entorno a esperar_y_abrir.bat
REM (que es .bat puro y no puede leer config.json solo) y por argumento a
REM splash.py (que si no recibe argv cae el solo a get_puerto(), el
REM preferido - ver splash.py).
set PUERTO=
for /f %%P in ('python -c "from config import get_puerto, puerto_libre; print(puerto_libre(get_puerto()))" 2^>nul') do set PUERTO=%%P
if not defined PUERTO set PUERTO=8000

echo.
echo Arrancando MCVL Analyser SEPE en http://127.0.0.1:%PUERTO%/ ...
REM El navegador se abre solo cuando el servidor ya responde de verdad (ver
REM esperar_y_abrir.bat) - abrirlo aqui a ciegas fallaba en PCs donde las
REM importaciones de python tardan varios segundos (antivirus corporativo).
REM La ventana de splash (splash.py) sondea el servidor igual que
REM esperar_y_abrir.bat y se cierra sola cuando responde. pythonw evita que
REM se abra una consola negra detras de la ventana; si el interprete no la
REM trae (venv creado sin ella), cae a python normal.
where pythonw >nul 2>nul
if errorlevel 1 (set PYTHON_SPLASH=python) else (set PYTHON_SPLASH=pythonw)
start "" %PYTHON_SPLASH% "%~dp0splash.py" %PUERTO%
REM /min: solo espera al servidor y abre el navegador, no hace falta que el
REM usuario la vea ni la cierre a mano - antes aparecia como una ventana de
REM consola suelta sin utilidad visible una vez arrancada la app.
start /min "" "%~dp0esperar_y_abrir.bat"
python run.py

pause

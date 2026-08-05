@echo off
REM Lanzador de la herramienta ETL con interfaz web (uso restringido, ver CLAUDE.md).
REM Mismo patron que el lanzador .bat de ForecastSEPE: instala dependencias si faltan y
REM arranca el servidor. Doble clic desde cualquier sitio (usa rutas relativas a este .bat).
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
cd /d "%~dp0..\.."

echo Comprobando dependencias de etl/requirements.txt...
python -m pip install -r etl\requirements.txt --quiet
if not errorlevel 1 goto :arrancar

echo Instalacion normal fallida, reintentando con host de confianza para PC corporativo...
python -m pip install -r etl\requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org --quiet
if not errorlevel 1 goto :arrancar

echo.
echo No se han podido instalar las dependencias. Si estas en el PC corporativo,
echo puede hacer falta el certificado interno - ver CLAUDE.md.
pause
exit /b 1

:arrancar
echo.
echo Arrancando MCVL ETL en http://127.0.0.1:8010/ ...
start "" http://127.0.0.1:8010/
python etl\webui\run.py

pause

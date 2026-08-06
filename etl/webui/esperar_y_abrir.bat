@echo off
REM Espera a que el servidor este realmente escuchando en el puerto 8010 antes
REM de abrir el navegador. Lanzado en paralelo (via start) desde iniciar.bat
REM mientras python arranca en primer plano: abrir el navegador a ciegas nada
REM mas lanzar python fallaba en PCs donde las importaciones (pandas, duckdb...)
REM tardan varios segundos (tipico con antivirus/EDR corporativo escaneando
REM cada import), porque el navegador no reintenta solo una conexion rechazada.
setlocal enabledelayedexpansion
set intentos=0

:esperar
set /a intentos+=1
if %intentos% gtr 40 goto :abrir
curl -s -o nul http://127.0.0.1:8010/
if errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto :esperar
)

:abrir
start "" http://127.0.0.1:8010/

@echo off
REM Espera a que el servidor este realmente escuchando antes de abrir el
REM navegador. Lanzado en paralelo (via start) desde iniciar.bat mientras
REM python arranca en primer plano: abrir el navegador a ciegas nada mas
REM lanzar python fallaba en PCs donde las importaciones (pandas, duckdb...)
REM tardan varios segundos (tipico con antivirus/EDR corporativo escaneando
REM cada import), porque el navegador no reintenta solo una conexion rechazada.
REM PUERTO llega heredado de iniciar.bat (resuelto ahi via config.get_puerto,
REM normalmente 8000) - si por lo que sea no llega, 8000 como red de seguridad.
setlocal enabledelayedexpansion
if not defined PUERTO set PUERTO=8000
set intentos=0

:esperar
set /a intentos+=1
if %intentos% gtr 40 goto :abrir
curl -s -o nul http://127.0.0.1:%PUERTO%/
if errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto :esperar
)

:abrir
start "" http://127.0.0.1:%PUERTO%/

@echo off
REM Reconstruye el .exe con PyInstaller y coloca junto a el los ficheros que
REM el propio build no genera. Usar SIEMPRE esto en vez de llamar a
REM "pyinstaller mcvlanalyser.spec" a mano.
REM
REM Motivo: PyInstaller BORRA y regenera build\dist\mcvlanalyser entero en
REM cada build (no solo actualiza lo que cambio) - cualquier fichero puesto
REM ahi a mano en un build anterior (crear_acceso_directo.bat) desaparece en
REM silencio si no se vuelve a copiar DESPUES de cada build. Descubierto el
REM 2026-08-28: un build normal borro esa copia sin avisar, y la unica fuente
REM que quedaba viva en ese momento estaba dentro de ese mismo dist/ - hubo
REM que recuperarla del historial de git. Ahora crear_acceso_directo.bat vive
REM versionado aqui, en build\ (no en build\dist\, que es gitignored), para
REM que este script siempre tenga de donde copiarlo.
setlocal
cd /d "%~dp0"

REM --workpath/--distpath explicitos: sin ellos PyInstaller usa por defecto
REM ".\build" (relativo al cwd, que aqui ya es la carpeta build\) como cache
REM de analisis - crea un build\build\ nuevo en vez de reusar build\work\,
REM el nombre ya documentado en CLAUDE.md y en .gitignore. Descubierto el
REM 2026-08-28 al escribir este mismo script.
python -m PyInstaller mcvlanalyser.spec --noconfirm --workpath work --distpath dist
if errorlevel 1 (
    echo.
    echo PyInstaller fallo - revisa el error de arriba.
    exit /b 1
)

copy /y "crear_acceso_directo.bat" "dist\mcvlanalyser\crear_acceso_directo.bat" >nul

echo.
echo Build listo en build\dist\mcvlanalyser\ (con crear_acceso_directo.bat incluido).

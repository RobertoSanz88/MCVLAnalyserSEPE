@echo off
REM Crea en el Escritorio un acceso directo a MCVLRefinery (iniciar.bat), con el
REM mismo icono y nombre que ya usa el PC de desarrollo - para que los compañeros
REM con acceso al ETL (uso restringido, ver CLAUDE.md) no tengan que montarlo a
REM mano cada uno. Ejecutar una vez tras el git pull, desde esta misma carpeta
REM (etl/webui/).
REM
REM Un .bat no puede crear un .lnk por si solo - se genera un .vbs temporal que
REM si sabe hacerlo via WScript.Shell (funciona en cualquier Windows sin tocar
REM politica de ejecucion, a diferencia de un script de PowerShell). Mismo
REM patron que build/crear_acceso_directo.bat de la app principal.
setlocal
cd /d "%~dp0"

if not exist "%~dp0iniciar.bat" (
    echo No se encuentra iniciar.bat junto a este .bat.
    echo Ejecuta crear_acceso_directo.bat desde etl\webui\ dentro del repo.
    pause
    exit /b 1
)

set VBS=%TEMP%\crear_acceso_mcvlrefinery.vbs

> "%VBS%" echo Set oWS = WScript.CreateObject("WScript.Shell")
>> "%VBS%" echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\MCVLRefinery.lnk"
>> "%VBS%" echo Set oLink = oWS.CreateShortcut(sLinkFile)
>> "%VBS%" echo oLink.TargetPath = "%~dp0iniciar.bat"
>> "%VBS%" echo oLink.WorkingDirectory = "%~dp0"
>> "%VBS%" echo oLink.IconLocation = "%~dp0static\icon.ico,0"
>> "%VBS%" echo oLink.Description = "Construccion de tablas Parquet de la MCVL (uso restringido)"
>> "%VBS%" echo oLink.Save

cscript //nologo "%VBS%"
del "%VBS%"

echo.
echo Acceso directo creado en el escritorio: MCVLRefinery
pause

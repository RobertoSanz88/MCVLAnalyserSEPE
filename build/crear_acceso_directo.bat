@echo off
REM Crea en el Escritorio un acceso directo igual al que usa el PC de
REM desarrollo (MCVLAnalyserSEPE.lnk: mismo icono, misma descripcion), pero
REM apuntando a mcvlanalyser.exe en vez de a iniciar.bat - para compa_neros
REM que reciben el .exe empaquetado (ver CLAUDE.md, distribucion via GitHub
REM Releases) en vez del repo con Python instalado.
REM
REM Pensado para vivir junto a mcvlanalyser.exe dentro del zip de la Release
REM (mismo nivel, no dentro de _internal): al extraer el zip, doble clic en
REM este .bat crea el acceso directo una sola vez.
REM
REM Un .bat no puede crear un .lnk por si solo - se genera un .vbs temporal
REM que si sabe hacerlo via WScript.Shell (funciona en cualquier Windows sin
REM tocar politica de ejecucion, a diferencia de un script de PowerShell).
setlocal
cd /d "%~dp0"

if not exist "%~dp0mcvlanalyser.exe" (
    echo No se encuentra mcvlanalyser.exe junto a este .bat.
    echo Ejecuta crear_acceso_directo.bat desde la carpeta donde has
    echo descomprimido el zip de la aplicacion.
    pause
    exit /b 1
)

set VBS=%TEMP%\crear_acceso_mcvl.vbs

> "%VBS%" echo Set oWS = WScript.CreateObject("WScript.Shell")
>> "%VBS%" echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\MCVLAnalyserSEPE.lnk"
>> "%VBS%" echo Set oLink = oWS.CreateShortcut(sLinkFile)
>> "%VBS%" echo oLink.TargetPath = "%~dp0mcvlanalyser.exe"
>> "%VBS%" echo oLink.WorkingDirectory = "%~dp0"
>> "%VBS%" echo oLink.IconLocation = "%~dp0_internal\app\static\icon.ico,0"
>> "%VBS%" echo oLink.Description = "Lanza la app de analisis MCVL (colectivos, trayectorias)"
>> "%VBS%" echo oLink.Save

cscript //nologo "%VBS%"
del "%VBS%"

echo.
echo Acceso directo creado en el escritorio: MCVLAnalyserSEPE
pause

@echo off
chcp 65001 >nul
title Compilador Standalone EXE - IRL Stream Server por Iamfreedz
color 0B

echo ===============================================================================
echo        COMPILADOR STANDALONE EXE - IRL STREAM OPERATIONS CENTER
echo        Autor: Iamfreedz  ^|  Kick: https://kick.com/iamfreedz
echo ===============================================================================
echo.
echo [1/3] Verificando dependencias necesarias (PyInstaller)...
python -m pip install --quiet --upgrade pyinstaller

echo [2/3] Compilando IRL_Server.exe en un único archivo ejecutable...
echo       (Esto puede tardar entre 20 y 45 segundos, por favor espera)...
echo.

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --console ^
    --name "IRL_Server" ^
    --version-file "version_info.txt" ^
    --add-data "web;web" ^
    --add-data "obs_plugin;obs_plugin" ^
    launcher_exe.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ===============================================================================
    echo  [OK] COMPILACION EXITOSA!
    echo ===============================================================================
    echo.
    echo Tu archivo ejecutable ha sido generado en:
    echo   ==^> dist\IRL_Server.exe
    echo.
    echo Autor: Iamfreedz (https://kick.com/iamfreedz)
    echo Puedes copiar "IRL_Server.exe" a cualquier computadora con Windows.
    echo No requiere tener Python, Git ni ninguna librería instalada.
    echo Al ejecutarse creará automáticamente la carpeta "obs_plugin\" y acceso directo.
    echo.
) else (
    echo.
    echo [!] Hubo un error durante la compilación. Revisa los mensajes anteriores.
)

echo Presiona cualquier tecla para salir...
pause >nul

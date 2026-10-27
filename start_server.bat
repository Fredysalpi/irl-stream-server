@echo off
title SAP NetWeaver IRL Streaming Hub - Servidor RTMP ^& Consola Fiori
color 0B
cls
echo ===============================================================================
echo       SAP NETWEAVER IRL STREAMING OPERATIONS CENTER ^& RTMP BROKER
echo ===============================================================================
echo.
echo  [i] Iniciando Servidor RTMP (Puerto 1935 TCP)...
echo  [i] Iniciando Consola Web SAP Fiori (Puerto 8080 HTTP)...
echo.
echo  [URL Consola SAP] : http://localhost:8080
echo.
echo  Abriendo panel de control en tu navegador predeterminado...
start http://localhost:8080
echo ===============================================================================
echo.
python rtmp_server.py
echo.
echo [!] El servidor se ha detenido.
pause

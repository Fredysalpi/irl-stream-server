"""
=============================================================================
IRL Stream Operations Center - Launcher & Instalador Portable Standalone (.exe)
=============================================================================
Autor Oficial: Iamfreedz
Canal Kick:    https://kick.com/iamfreedz
Sistema:       IRL [PRD/V1] - Anti-Cortes RTMP Broker
=============================================================================
"""

import os
import sys
import time
import shutil
import threading
import webbrowser
import asyncio
import subprocess

# Determinar directorios de ejecución (Standalone vs Código Fuente)
if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", EXE_DIR)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = EXE_DIR

# Fijar el directorio de trabajo donde reside el archivo .exe
os.chdir(EXE_DIR)

# Banner Brandeado de Instalación y Lanzamiento
def print_branding_header():
    print("=" * 80)
    print("      IRL STREAM OPERATIONS CENTER - SERVIDOR & INSTALADOR PORTABLE")
    print("=" * 80)
    print("  [>] Autor Oficial : Iamfreedz")
    print("  [>] Canal de Kick : https://kick.com/iamfreedz")
    print("  [>] Versión       : PRD/V1 (Windows Standalone 64-bit)")
    print("-" * 80)

print_branding_header()

# Instalar / Extraer automáticamente la carpeta del plugin de OBS
local_plugin = os.path.join(EXE_DIR, "obs_plugin")
bundle_plugin = os.path.join(BUNDLE_DIR, "obs_plugin")

try:
    os.makedirs(local_plugin, exist_ok=True)
    if os.path.exists(bundle_plugin):
        for item in os.listdir(bundle_plugin):
            s_file = os.path.join(bundle_plugin, item)
            d_file = os.path.join(local_plugin, item)
            if not os.path.exists(d_file) and os.path.isfile(s_file):
                shutil.copy2(s_file, d_file)
    # Asegurar que live_status.txt exista
    status_txt = os.path.join(local_plugin, "live_status.txt")
    if not os.path.exists(status_txt):
        with open(status_txt, "w", encoding="utf-8") as f:
            f.write("OFFLINE\n")
    print("  [+] Carpeta de OBS lista en: obs_plugin\\")
except Exception as e:
    print(f"  [!] Aviso al preparar obs_plugin: {e}")

# Crear acceso directo en el Escritorio en la primera ejecución
def ensure_desktop_shortcut():
    if not getattr(sys, "frozen", False):
        return
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, "IRL Stream Server.lnk")
        if not os.path.exists(shortcut_path):
            exe_path = sys.executable
            ps_cmd = (
                f'$ws = New-Object -ComObject WScript.Shell; '
                f'$s = $ws.CreateShortcut("{shortcut_path}"); '
                f'$s.TargetPath = "{exe_path}"; '
                f'$s.WorkingDirectory = "{EXE_DIR}"; '
                f'$s.Description = "IRL Stream Server por Iamfreedz (kick.com/iamfreedz)"; '
                f'$s.Save()'
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=4)
            print("  [+] Acceso directo creado en tu Escritorio: 'IRL Stream Server.lnk'")
    except Exception:
        pass

ensure_desktop_shortcut()

# Importar el servidor original sin modificar sus archivos
import rtmp_server

# Redirigir rutas de persistencia al directorio donde el usuario tiene el .exe
rtmp_server.BASE_DIR = EXE_DIR
rtmp_server.CONFIG_FILE = os.path.join(EXE_DIR, "config.json")

# Para recursos estáticos web: si existen junto al .exe los usa, sino los empaquetados
local_web = os.path.join(EXE_DIR, "web")
bundle_web = os.path.join(BUNDLE_DIR, "web")
rtmp_server.WEB_DIR = local_web if os.path.exists(local_web) else bundle_web
rtmp_server.PLUGIN_DIR = local_plugin
rtmp_server.STATUS_TXT_FILE = os.path.join(local_plugin, "live_status.txt")
rtmp_server.STATUS_JSON_FILE = os.path.join(local_plugin, "live_status.json")

def open_browser_delayed():
    """Abre el panel en el navegador predeterminado tras 1.2 segundos."""
    time.sleep(1.2)
    try:
        webbrowser.open("http://localhost:8080")
    except Exception:
        pass

if __name__ == "__main__":
    # Lanzar hilo en segundo plano para abrir el navegador
    threading.Thread(target=open_browser_delayed, daemon=True).start()
    
    # Iniciar servidor RTMP asíncrono
    try:
        asyncio.run(rtmp_server.main())
    except KeyboardInterrupt:
        print("\n[i] Servidor detenido por el usuario.")
    except Exception as e:
        print(f"\n[!] Error fatal en la ejecución: {e}")
        input("Presiona ENTER para salir...")

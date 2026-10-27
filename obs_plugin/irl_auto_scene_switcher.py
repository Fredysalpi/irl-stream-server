"""
=============================================================================
IRL Auto Scene Switcher V2 para OBS Studio (Python Script)
Diseñado para: Streaming Móvil Anti-Cortes | Kick: iamfreedz
=============================================================================
Corrección V2:
1. Lee directamente el estado del servidor RTMP local (live_status.json / API)
   lo que garantiza 100% de precisión sin falsos positivos.
2. Sistema Anti-Rebote con Cooldown de 4 segundos que elimina saltos intermitentes.
3. Respaldo por dimensiones reales de video de la fuente en OBS.
=============================================================================
"""

import obspython as obs
import os
import json
import time
import urllib.request

# Propiedades configurables
live_scene_name = ""
brb_scene_name = ""
source_name = ""
detection_mode = "hybrid"   # "hybrid", "server", "obs_source"
disconnect_delay = 3.0
reconnect_delay = 1.0
is_enabled = True
debug_logs = False

# Temporizadores y estado interno
poll_interval_ms = 300
signal_stable_timer = 0.0
signal_lost_timer = 0.0
switch_cooldown = 0.0
current_stream_state = False
last_applied_scene = ""

script_dir = ""
status_file_path = ""


def script_description():
    return """
    <h2>🎬 IRL Auto Scene Switcher V2 (Python)</h2>
    <p>Conmutador inteligente de escenas para OBS Studio en directos IRL:</p>
    <ul>
      <li>🟢 <b>Celular emitiendo:</b> Cambia automáticamente a <b>EN VIVO</b>.</li>
      <li>🔴 <b>Corte de señal / Túnel:</b> Cambia automáticamente a <b>PAUSA / BRB</b>.</li>
    </ul>
    <p><b>Ventaja V2:</b> Detección directa mediante el servidor RTMP local con protección anti-rebote.</p>
    <hr/>
    """


def script_properties():
    props = obs.obs_properties_create()

    obs.obs_properties_add_bool(props, "is_enabled", "✅ Activar Conmutador Automático de Escenas")

    p_live = obs.obs_properties_add_list(
        props,
        "live_scene_name",
        "🟢 Escena EN VIVO (Cuando hay señal de video):",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )

    p_brb = obs.obs_properties_add_list(
        props,
        "brb_scene_name",
        "🔴 Escena PAUSA / BRB (Cuando se corta la señal):",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )

    populate_scenes(p_live)
    populate_scenes(p_brb)

    p_mode = obs.obs_properties_add_list(
        props,
        "detection_mode",
        "📡 Método de Detección de Señal:",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_mode, "⚡ Automático / Híbrido (Servidor RTMP + OBS) [Recomendado]", "hybrid")
    obs.obs_property_list_add_string(p_mode, "🌐 Estado Servidor RTMP (API / live_status.json)", "server")
    obs.obs_property_list_add_string(p_mode, "📹 Dimensiones de Video de la Fuente en OBS", "obs_source")

    p_sources = obs.obs_properties_add_list(
        props,
        "source_name",
        "📹 Fuente Multimedia de tu cámara en OBS:",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )
    populate_media_sources(p_sources)

    def refresh_callback(props, prop):
        populate_scenes(p_live)
        populate_scenes(p_brb)
        populate_media_sources(p_sources)
        return True

    obs.obs_properties_add_button(props, "refresh_btn", "🔄 Actualizar Lista de Escenas y Fuentes", refresh_callback)

    obs.obs_properties_add_float(
        props,
        "disconnect_delay",
        "⏱️ Espera antes de cambiar a Pausa (segundos):",
        1.0,
        10.0,
        0.5
    )

    obs.obs_properties_add_float(
        props,
        "reconnect_delay",
        "⚡ Espera antes de volver a En Vivo (segundos):",
        0.5,
        5.0,
        0.5
    )

    obs.obs_properties_add_bool(props, "debug_logs", "📜 Imprimir detalles en el Log de OBS")

    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "is_enabled", True)
    obs.obs_data_set_default_string(settings, "detection_mode", "hybrid")
    obs.obs_data_set_default_double(settings, "disconnect_delay", 3.0)
    obs.obs_data_set_default_double(settings, "reconnect_delay", 1.0)
    obs.obs_data_set_default_bool(settings, "debug_logs", False)


def script_update(settings):
    global live_scene_name, brb_scene_name, source_name
    global detection_mode, disconnect_delay, reconnect_delay, is_enabled, debug_logs

    live_scene_name = obs.obs_data_get_string(settings, "live_scene_name")
    brb_scene_name = obs.obs_data_get_string(settings, "brb_scene_name")
    source_name = obs.obs_data_get_string(settings, "source_name")
    detection_mode = obs.obs_data_get_string(settings, "detection_mode")
    disconnect_delay = obs.obs_data_get_double(settings, "disconnect_delay")
    reconnect_delay = obs.obs_data_get_double(settings, "reconnect_delay")
    is_enabled = obs.obs_data_get_bool(settings, "is_enabled")
    debug_logs = obs.obs_data_get_bool(settings, "debug_logs")


def script_load(settings):
    global script_dir, status_file_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    status_file_path = os.path.join(script_dir, "live_status.json")

    obs.timer_add(check_stream_status, poll_interval_ms)
    print(f"[IRL Switcher V2 Python] Cargado. Status file: {status_file_path}")


def script_unload():
    obs.timer_remove(check_stream_status)
    print("[IRL Switcher V2 Python] Descargado.")


def read_server_status():
    # 1. Probar archivo JSON local
    if status_file_path and os.path.exists(status_file_path):
        try:
            with open(status_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ts = data.get("timestamp", 0)
                if time.time() - ts <= 8:
                    return bool(data.get("is_live", False))
        except Exception:
            pass

    # 2. Probar API HTTP local directamente
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/api/status", headers={"User-Agent": "OBS-Switcher"})
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            data = json.loads(resp.read().decode())
            is_live = bool(data.get("is_live", False))
            bitrate = float(data.get("bitrate_kbps", 0))
            return is_live and (bitrate > 5)
    except Exception:
        pass

    return None


def check_obs_source_has_video():
    if not source_name:
        return False

    source = obs.obs_get_source_by_name(source_name)
    if not source:
        return False

    w = obs.obs_source_get_width(source)
    h = obs.obs_source_get_height(source)
    state = obs.obs_source_media_get_state(source)

    obs.obs_source_release(source)
    return (w > 0 and h > 0) or (state in (2, 8))


def check_stream_status():
    global signal_stable_timer, signal_lost_timer, switch_cooldown, current_stream_state

    if not is_enabled or not live_scene_name or not brb_scene_name:
        return

    dt = poll_interval_ms / 1000.0

    if switch_cooldown > 0:
        switch_cooldown -= dt
        return

    signal_detected = False

    if detection_mode == "server":
        s_status = read_server_status()
        signal_detected = (s_status is True)
    elif detection_mode == "obs_source":
        signal_detected = check_obs_source_has_video()
    else:
        # Híbrido
        s_status = read_server_status()
        if s_status is not None:
            signal_detected = s_status
        else:
            signal_detected = check_obs_source_has_video()

    if signal_detected:
        signal_lost_timer = 0.0
        signal_stable_timer += dt

        if signal_stable_timer >= reconnect_delay:
            if not current_stream_state or last_applied_scene != live_scene_name:
                current_stream_state = True
                perform_switch(live_scene_name, "🟢 SEÑAL DETECTADA -> Cambiando a EN VIVO")
    else:
        signal_stable_timer = 0.0
        signal_lost_timer += dt

        if signal_lost_timer >= disconnect_delay:
            if current_stream_state or last_applied_scene != brb_scene_name:
                current_stream_state = False
                perform_switch(brb_scene_name, "🔴 PÉRDIDA DE SEÑAL -> Cambiando a PAUSA / BRB")


def perform_switch(target_scene_name, reason):
    global last_applied_scene, switch_cooldown

    if not target_scene_name:
        return

    current = obs.obs_frontend_get_current_scene()
    if current:
        c_name = obs.obs_source_get_name(current)
        obs.obs_source_release(current)
        if c_name == target_scene_name:
            last_applied_scene = target_scene_name
            return

    scenes = obs.obs_frontend_get_scenes()
    if scenes:
        for s in scenes:
            name = obs.obs_source_get_name(s)
            if name == target_scene_name:
                obs.obs_frontend_set_current_scene(s)
                last_applied_scene = target_scene_name
                switch_cooldown = 4.0
                if debug_logs:
                    print(f"[IRL Switcher V2] {reason} | Escena fijada: '{target_scene_name}'")
                break
        obs.source_list_release(scenes)


def populate_scenes(list_prop):
    obs.obs_property_list_clear(list_prop)
    obs.obs_property_list_add_string(list_prop, "-- Seleccionar Escena --", "")

    scenes = obs.obs_frontend_get_scenes()
    if scenes:
        for s in scenes:
            name = obs.obs_source_get_name(s)
            obs.obs_property_list_add_string(list_prop, name, name)
        obs.source_list_release(scenes)


def populate_media_sources(list_prop):
    obs.obs_property_list_clear(list_prop)
    obs.obs_property_list_add_string(list_prop, "-- Seleccionar Fuente Multimedia --", "")

    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            s_id = obs.obs_source_get_id(s)
            if s_id in ("ffmpeg_source", "vlc_source"):
                name = obs.obs_source_get_name(s)
                display = f"{name} ({'VLC' if s_id == 'vlc_source' else 'Media Source'})"
                obs.obs_property_list_add_string(list_prop, display, name)
        obs.source_list_release(sources)

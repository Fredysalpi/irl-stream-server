-- =============================================================================
-- IRL Auto Scene Switcher V2 para OBS Studio (Lua Script)
-- Diseñado para: Streaming Móvil Anti-Cortes | Kick: iamfreedz
-- =============================================================================
-- Corrección de problemas de bucle/intermitencia y detección invertida:
-- 1. Lee directamente el estado del Servidor RTMP local (live_status.txt)
--    lo que garantiza 100% de precisión sin depender de si la fuente está
--    visible o cerrada en otra escena.
-- 2. Sistema Anti-Rebote (Cooldown de 4 segundos) para evitar que OBS
--    salte entre escenas en bucle mientras el video carga.
-- 3. Detección híbrida: Servidor RTMP + Dimensiones reales de video en OBS.
-- =============================================================================

obs = obslua

-- Propiedades configurables
local live_scene_name = ""
local brb_scene_name = ""
local source_name = ""
local detection_mode = "hybrid"   -- "hybrid", "server", "obs_source"
local disconnect_delay = 3.0      -- Segundos sin señal antes de ir a Pausa
local reconnect_delay = 1.0       -- Segundos de señal estable antes de ir a En Vivo
local is_enabled = true
local debug_logs = false

-- Temporizadores y estado interno
local poll_interval_ms = 300      -- Monitoreo cada 300ms
local signal_stable_timer = 0.0
local signal_lost_timer = 0.0
local switch_cooldown = 0.0       -- Cooldown anti-rebote (segundos)
local current_stream_state = false -- true = LIVE, false = OFFLINE
local last_applied_scene = ""
local status_display_prop = nil

-- Ruta del archivo de estado del servidor
local script_dir = ""
local status_file_path = ""

-- =============================================================================
-- DESCRIPCIÓN EN LA VENTANA DE OBS
-- =============================================================================
function script_description()
    return [[
<h2>🎬 IRL Auto Scene Switcher V2 (Anti-Cortes)</h2>
<p>Sistema inteligente de cambio de escenas para directos IRL en Kick/Twitch:</p>
<ul>
  <li>🟢 <b>Celular emitiendo (4G/5G):</b> Cambia automáticamente a <b>EN VIVO</b>.</li>
  <li>🔴 <b>Corte de señal / Túnel:</b> Cambia automáticamente a <b>PAUSA / VOLVEMOS ENSEGUIDA</b>.</li>
</ul>
<p><b>Ventaja V2:</b> Se comunica directamente con el servidor RTMP local para evitar parpadeos y bucles infinitos entre escenas.</p>
<hr/>
    ]]
end

-- =============================================================================
-- PROPIEDADES EN LA INTERFAZ DE OBS
-- =============================================================================
function script_properties()
    local props = obs.obs_properties_create()

    -- Checkbox de activación
    obs.obs_properties_add_bool(props, "is_enabled", "✅ Activar Conmutador Automático de Escenas")

    -- Selector de Escena En Vivo
    local p_live = obs.obs_properties_add_list(
        props,
        "live_scene_name",
        "🟢 Escena EN VIVO (Cuando hay señal de video):",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )

    -- Selector de Escena Pausa
    local p_brb = obs.obs_properties_add_list(
        props,
        "brb_scene_name",
        "🔴 Escena PAUSA / BRB (Cuando se corta la señal):",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )

    populate_scenes(p_live)
    populate_scenes(p_brb)

    -- Modo de detección
    local p_mode = obs.obs_properties_add_list(
        props,
        "detection_mode",
        "📡 Método de Detección de Señal:",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_mode, "⚡ Automático / Híbrido (Servidor RTMP + OBS) [Recomendado]", "hybrid")
    obs.obs_property_list_add_string(p_mode, "🌐 Estado Servidor RTMP (live_status.txt)", "server")
    obs.obs_property_list_add_string(p_mode, "📹 Dimensiones de Video de la Fuente en OBS", "obs_source")

    -- Selector de Fuente Multimedia en OBS
    local p_sources = obs.obs_properties_add_list(
        props,
        "source_name",
        "📹 Fuente Multimedia de tu cámara en OBS:",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING
    )
    populate_media_sources(p_sources)

    -- Botón de refrescar listas
    obs.obs_properties_add_button(props, "refresh_lists_btn", "🔄 Actualizar Lista de Escenas y Fuentes", function()
        populate_scenes(p_live)
        populate_scenes(p_brb)
        populate_media_sources(p_sources)
        return true
    end)

    -- Tiempos de margen (Anti-parpadeo)
    local p_delay_disc = obs.obs_properties_add_float(
        props,
        "disconnect_delay",
        "⏱️ Espera antes de cambiar a Pausa (segundos):",
        1.0,
        10.0,
        0.5
    )
    obs.obs_property_set_long_description(
        p_delay_disc,
        "Tiempo sin señal antes de pasar a Pausa. Da margen para que un microcorte de 1-2 segundos no cambie de escena bruscamente. Recomendado: 3.0s."
    )

    local p_delay_rec = obs.obs_properties_add_float(
        props,
        "reconnect_delay",
        "⚡ Espera antes de volver a En Vivo (segundos):",
        0.5,
        5.0,
        0.5
    )
    obs.obs_property_set_long_description(
        p_delay_rec,
        "Tiempo de señal continua antes de regresar al directo para asegurar que el video ya esté decodificando fluido. Recomendado: 1.0s."
    )

    -- Mostrar logs
    obs.obs_properties_add_bool(props, "debug_logs", "📜 Imprimir detalles en el Log de OBS")

    return props
end

-- =============================================================================
-- VALORES POR DEFECTO
-- =============================================================================
function script_defaults(settings)
    obs.obs_data_set_default_bool(settings, "is_enabled", true)
    obs.obs_data_set_default_string(settings, "detection_mode", "hybrid")
    obs.obs_data_set_default_double(settings, "disconnect_delay", 3.0)
    obs.obs_data_set_default_double(settings, "reconnect_delay", 1.0)
    obs.obs_data_set_default_bool(settings, "debug_logs", false)
end

-- =============================================================================
-- ACTUALIZACIÓN DE CONFIGURACIÓN
-- =============================================================================
function script_update(settings)
    live_scene_name = obs.obs_data_get_string(settings, "live_scene_name")
    brb_scene_name = obs.obs_data_get_string(settings, "brb_scene_name")
    source_name = obs.obs_data_get_string(settings, "source_name")
    detection_mode = obs.obs_data_get_string(settings, "detection_mode")
    disconnect_delay = obs.obs_data_get_double(settings, "disconnect_delay")
    reconnect_delay = obs.obs_data_get_double(settings, "reconnect_delay")
    is_enabled = obs.obs_data_get_bool(settings, "is_enabled")
    debug_logs = obs.obs_data_get_bool(settings, "debug_logs")

    if debug_logs then
        obs.blog(obs.LOG_INFO, string.format("[IRL Switcher V2] Config actualizada: Live='%s', BRB='%s', Mode='%s'",
            live_scene_name, brb_scene_name, detection_mode))
    end
end

-- =============================================================================
-- CARGA Y DESCARGA
-- =============================================================================
function script_load(settings)
    -- Obtener la ruta del directorio del script para localizar live_status.txt
    script_dir = script_path()
    status_file_path = script_dir .. "live_status.txt"

    obs.timer_add(check_stream_status, poll_interval_ms)
    obs.blog(obs.LOG_INFO, string.format("[IRL Switcher V2] Iniciado. Monitoreando status en: %s", status_file_path))
end

function script_unload()
    obs.timer_remove(check_stream_status)
    obs.blog(obs.LOG_INFO, "[IRL Switcher V2] Detenido.")
end

-- =============================================================================
-- LECTURA DEL ARCHIVO DE ESTADO DEL SERVIDOR RTMP
-- =============================================================================
function read_server_status_file()
    if status_file_path == nil or status_file_path == "" then
        return nil
    end

    local file = io.open(status_file_path, "r")
    if file == nil then
        return nil
    end

    local line1 = file:read("*line") -- "LIVE" o "OFFLINE"
    local line2 = file:read("*line") -- Bitrate (ej: "3250.0")
    local line3 = file:read("*line") -- Timestamp unix
    file:close()

    if line1 == nil then return nil end
    local is_live_str = string.upper(string.match(line1, "^%s*(.-)%s*$") or "")

    -- Verificar que el archivo no esté viejo (máximo 8 segundos de antigüedad)
    local ts = tonumber(line3)
    local current_time = os.time()
    if ts ~= nil and (current_time - ts > 8) then
        -- El servidor RTMP podría estar apagado
        return nil
    end

    return (is_live_str == "LIVE")
end

-- =============================================================================
-- VERIFICACIÓN DEL ESTADO DE LA FUENTE EN OBS (FALLBACK)
-- =============================================================================
function check_obs_source_has_video()
    if source_name == nil or source_name == "" then
        return false
    end

    local source = obs.obs_get_source_by_name(source_name)
    if source == nil then
        return false
    end

    -- 1. Verificar dimensiones reales del cuadro de video decodificado
    local width = obs.obs_source_get_width(source)
    local height = obs.obs_source_get_height(source)
    local has_dimensions = (width > 0 and height > 0)

    -- 2. Verificar media_state (2 = PLAYING, 8 = BUFFERING)
    local state = obs.obs_source_media_get_state(source)
    local is_playing = (state == 2 or state == 8)

    obs.obs_source_release(source)

    return (has_dimensions or is_playing)
end

-- =============================================================================
-- BUCLE PRINCIPAL DE VERIFICACIÓN (CADA 300ms)
-- =============================================================================
function check_stream_status()
    if not is_enabled then return end
    if live_scene_name == nil or live_scene_name == "" then return end
    if brb_scene_name == nil or brb_scene_name == "" then return end

    local dt = poll_interval_ms / 1000.0

    -- Si estamos en período de Cooldown (Anti-Rebote tras un cambio de escena), solo esperar
    if switch_cooldown > 0 then
        switch_cooldown = switch_cooldown - dt
        return
    end

    -- Determinar si hay señal según el modo configurado
    local signal_detected = false

    if detection_mode == "server" then
        local s_status = read_server_status_file()
        signal_detected = (s_status == true)
    elseif detection_mode == "obs_source" then
        signal_detected = check_obs_source_has_video()
    else
        -- Modo Híbrido (Prioridad Servidor RTMP, con respaldo en Fuente OBS)
        local s_status = read_server_status_file()
        if s_status ~= nil then
            signal_detected = s_status
        else
            signal_detected = check_obs_source_has_video()
        end
    end

    -- Evaluar temporizadores de estabilidad
    if signal_detected then
        -- 🟢 HAY SEÑAL / VIDEO
        signal_lost_timer = 0.0
        signal_stable_timer = signal_stable_timer + dt

        -- Si la señal se mantiene estable por reconnect_delay, cambiar a EN VIVO
        if signal_stable_timer >= reconnect_delay then
            if not current_stream_state or last_applied_scene ~= live_scene_name then
                current_stream_state = true
                perform_switch(live_scene_name, "🟢 SEÑAL DETECTADA -> Cambiando a EN VIVO")
            end
        end
    else
        -- 🔴 NO HAY SEÑAL
        signal_stable_timer = 0.0
        signal_lost_timer = signal_lost_timer + dt

        -- Si la falta de señal persiste por disconnect_delay, cambiar a PAUSA
        if signal_lost_timer >= disconnect_delay then
            if current_stream_state or last_applied_scene ~= brb_scene_name then
                current_stream_state = false
                perform_switch(brb_scene_name, "🔴 PÉRDIDA DE SEÑAL -> Cambiando a PAUSA / BRB")
            end
        end
    end
end

-- =============================================================================
-- CAMBIO DE ESCENA SEGURO (CON PROTECCIÓN ANTI-BUCLE)
-- =============================================================================
function perform_switch(target_scene_name, reason)
    if target_scene_name == nil or target_scene_name == "" then return end

    -- 1. Obtener la escena activa en OBS en este momento
    local current_scene_source = obs.obs_frontend_get_current_scene()
    if current_scene_source ~= nil then
        local current_name = obs.obs_source_get_name(current_scene_source)
        obs.obs_source_release(current_scene_source)

        -- Si OBS ya está en la escena deseada, no forzar cambio
        if current_name == target_scene_name then
            last_applied_scene = target_scene_name
            return
        end
    end

    -- 2. Buscar la escena de destino y activarla
    local scenes = obs.obs_frontend_get_scenes()
    if scenes ~= nil then
        for _, s in ipairs(scenes) do
            local name = obs.obs_source_get_name(s)
            if name == target_scene_name then
                obs.obs_frontend_set_current_scene(s)
                last_applied_scene = target_scene_name

                -- 🛡️ ACTIVAR COOLDOWN DE 4 SEGUNDOS:
                -- Durante 4 segundos NO se permite ningún cambio de escena.
                -- Esto permite que la transición de OBS termine y que la fuente multimedia
                -- empiece a recibir cuadros de video sin saltar de nuevo a Pausa.
                switch_cooldown = 4.0

                if debug_logs then
                    obs.blog(obs.LOG_INFO, string.format("[IRL Switcher V2] %s | Escena fijada: '%s' (Cooldown 4s activo)", reason, target_scene_name))
                end
                break
            end
        end
        obs.source_list_release(scenes)
    end
end

-- =============================================================================
-- UTILIDADES: POBLAR ESCENAS Y FUENTES
-- =============================================================================
function populate_scenes(list_prop)
    obs.obs_property_list_clear(list_prop)
    obs.obs_property_list_add_string(list_prop, "-- Seleccionar Escena --", "")

    local scenes = obs.obs_frontend_get_scenes()
    if scenes ~= nil then
        for _, s in ipairs(scenes) do
            local s_name = obs.obs_source_get_name(s)
            obs.obs_property_list_add_string(list_prop, s_name, s_name)
        end
        obs.source_list_release(scenes)
    end
end

function populate_media_sources(list_prop)
    obs.obs_property_list_clear(list_prop)
    obs.obs_property_list_add_string(list_prop, "-- Seleccionar Fuente Multimedia --", "")

    local sources = obs.obs_enum_sources()
    if sources ~= nil then
        for _, s in ipairs(sources) do
            local s_id = obs.obs_source_get_id(s)
            if s_id == "ffmpeg_source" or s_id == "vlc_source" then
                local s_name = obs.obs_source_get_name(s)
                local display = string.format("%s (%s)", s_name, s_id == "vlc_source" and "VLC" or "Media Source")
                obs.obs_property_list_add_string(list_prop, display, s_name)
            end
        end
        obs.source_list_release(sources)
    end
end

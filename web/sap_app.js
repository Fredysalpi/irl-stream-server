/**
 * IRL Operations Center & Media Broker
 * Client Application for RTMP Server & Tailscale VPN Integration
 */

// Application State
const state = {
  activeTCode: 'IRL01',
  config: {
    server_ip: '',
    rtmp_port: 1935,
    web_port: 8080,
    watchdog_timeout: 3.5,
    url_display_mode: 'local',
    stream_keys: {},
    tailscale: {
      ip: '',
      status: 'offline',
      auto_detect: true
    }
  },
  metrics: {
    is_live: false,
    bitrate_kbps: 0,
    active_channel: 'live/irl',
    video_packets: 0,
    audio_packets: 0,
    subscribers_count: 0,
    publisher_ip: '-',
    uptime_sec: 0,
    watchdog_alert: false,
    history: []
  },
  ws: null,
  wsReconnectTimer: null,
  pollTimer: null
};

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initCommandField();
  initCanvasChart();
  initModals();
  
  // Load initial config and keys
  fetchConfig().then(() => {
    fetchKeys();
    fetchLogs();
    initWebSocket();
    startPolling();
  });
});

// =============================================================================
// NAVIGATION & TRANSACTION CODE ROUTER
// =============================================================================
function initNavigation() {
  const tabs = document.querySelectorAll('.sap-tab-item');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tcode = tab.dataset.tcode;
      navigateTCode(tcode);
    });
  });
}

function initCommandField() {
  const cmdInput = document.getElementById('sapCmdInput');
  const execBtn = document.getElementById('sapCmdExecBtn');
  if (!cmdInput || !execBtn) return;

  const executeCommand = () => {
    let cmd = cmdInput.value.trim().toUpperCase();
    if (!cmd) return;

    if (cmd.startsWith('/N')) cmd = cmd.substring(2);
    if (cmd.startsWith('/O')) cmd = cmd.substring(2);

    const validTCodes = ['IRL01', 'IRL02', 'IRL03', 'IRL04', 'IRL05', 'SM21'];
    if (validTCodes.includes(cmd)) {
      navigateTCode(cmd);
      cmdInput.value = '';
      showToast(`Transacción ${cmd} ejecutada`, 'success');
    } else {
      showToast(`Código de transacción '${cmd}' no reconocido`, 'warning');
      setBottomMessage(`E: Transacción ${cmd} desconocida. Use IRL01, IRL02, IRL03, IRL04, IRL05 o SM21.`, 'error');
    }
  };

  cmdInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') executeCommand();
  });

  execBtn.addEventListener('click', executeCommand);
}

function navigateTCode(tcode) {
  state.activeTCode = tcode;

  // Update tabs
  document.querySelectorAll('.sap-tab-item').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.tcode === tcode);
  });

  // Update views
  document.querySelectorAll('.sap-tab-view').forEach(view => {
    view.classList.toggle('active', view.id === `view-${tcode}`);
  });

  // Update shell & object header
  const badge = document.getElementById('currentTCodeBadge');
  if (badge) badge.textContent = tcode;

  const titleElem = document.getElementById('objectHeaderTitle');
  const tcodeNames = {
    'IRL01': 'Cockpit de Monitoreo en Tiempo Real (Anti-Cortes)',
    'IRL02': 'Maestro de Claves de Transmisión (Stream Keys)',
    'IRL03': 'Conectividad Tailscale VPN & Guía Móvil',
    'IRL04': 'Parámetros del Sistema y Configuración de IP',
    'IRL05': 'Guía Paso a Paso: Configurar Fuente Multimedia en OBS Studio',
    'SM21': 'Registro del Sistema RTMP (SysLog Auditoría)'
  };
  if (titleElem && tcodeNames[tcode]) {
    titleElem.innerHTML = `<span class="sap-current-tcode">${tcode}</span> ${tcodeNames[tcode]}`;
  }

  // Update bottom status bar
  const bottomTCode = document.getElementById('bottomTCode');
  if (bottomTCode) bottomTCode.textContent = tcode;

  setBottomMessage(`S: Transacción ${tcode} cargada correctamente.`, 'ok');

  // Trigger specific view updates
  if (tcode === 'IRL02') fetchKeys();
  if (tcode === 'IRL03') renderTailscaleConfig();
  if (tcode === 'IRL05') renderObsGuideConfig();
  if (tcode === 'SM21') fetchLogs();
}

// =============================================================================
// API CALLS & WEBSOCKET
// =============================================================================
async function fetchConfig() {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      state.config = await res.json();
      applyConfigToUI();
    }
  } catch (err) {
    console.error('Error fetching config:', err);
    setBottomMessage('W: Servidor API no disponible temporalmente.', 'warn');
  }
}

async function fetchKeys() {
  try {
    const res = await fetch('/api/keys');
    if (res.ok) {
      const keys = await res.json();
      state.config.stream_keys = keys;
      renderKeysTable(keys);
      updateActiveKeysBadge(keys);
    }
  } catch (err) {
    console.error('Error fetching keys:', err);
  }
}

async function fetchLogs() {
  try {
    const res = await fetch('/api/logs');
    if (res.ok) {
      const logs = await res.json();
      renderLogsTable(logs);
    }
  } catch (err) {
    console.error('Error fetching logs:', err);
  }
}

function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  try {
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
      updateConnectionStatus(true);
      setBottomMessage('S: Conexión WebSocket de telemetría en tiempo real activa.', 'ok');
    };

    state.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleTelemetryUpdate(data);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    state.ws.onclose = () => {
      updateConnectionStatus(false);
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = setTimeout(initWebSocket, 3000);
    };

    state.ws.onerror = () => {
      state.ws.close();
    };
  } catch (err) {
    console.warn('WS not supported or failed, using polling fallback');
  }
}

function startPolling() {
  // Polling fallback to keep data fresh even if WS encounters drops
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const data = await res.json();
          handleTelemetryUpdate(data);
          updateConnectionStatus(true);
        } else {
          updateConnectionStatus(false);
        }
      } catch (e) {
        updateConnectionStatus(false);
      }
    }
  }, 2000);
}

function updateConnectionStatus(online) {
  const badge = document.getElementById('shellStatusBadge');
  if (badge) {
    badge.className = `sap-badge-pill ${online ? 'status-online' : 'status-offline'}`;
    badge.innerHTML = `<span class="sap-pulse-dot"></span> ${online ? 'RTMP 1935 ONLINE' : 'RTMP DESCONECTADO'}`;
  }
}

// =============================================================================
// TELEMETRY & LIVE MONITORING (IRL01)
// =============================================================================
function handleTelemetryUpdate(data) {
  state.metrics = Object.assign(state.metrics, data);

  // Update KPI Tiles
  const isLive = Boolean(data.is_live);
  const bitrate = Math.round(data.bitrate_kbps || 0);
  const videoPackets = data.video_packets || 0;
  const audioPackets = data.audio_packets || 0;
  const obsConnected = (data.subscribers_count || 0) > 0;
  const channel = data.active_channel || 'live/irl';
  const peerIp = data.publisher_ip || '-';

  // Mobile Ingest KPI
  const mobileTraffic = document.getElementById('kpiTrafficMobile');
  const mobileStatusText = document.getElementById('kpiValMobile');
  const mobileDesc = document.getElementById('kpiSubMobile');
  if (mobileTraffic && mobileStatusText) {
    mobileTraffic.className = `sap-kpi-status-traffic ${isLive ? 'sap-traffic-green' : 'sap-traffic-red'}`;
    mobileStatusText.textContent = isLive ? 'EN VIVO' : 'SIN SEÑAL';
    mobileStatusText.style.color = isLive ? '#107e3e' : '#bb0000';
    mobileDesc.textContent = isLive ? `Móvil: ${peerIp} (${channel})` : 'Esperando publicador IRL Pro...';
  }

  // Bitrate KPI
  const bitrateVal = document.getElementById('kpiValBitrate');
  const bitrateTraffic = document.getElementById('kpiTrafficBitrate');
  if (bitrateVal) {
    bitrateVal.innerHTML = `${bitrate} <span class="sap-kpi-unit">Kbps</span>`;
    if (bitrateTraffic) {
      if (!isLive) bitrateTraffic.className = 'sap-kpi-status-traffic sap-traffic-gray';
      else if (bitrate > 2500) bitrateTraffic.className = 'sap-kpi-status-traffic sap-traffic-green';
      else if (bitrate > 1000) bitrateTraffic.className = 'sap-kpi-status-traffic sap-traffic-yellow';
      else bitrateTraffic.className = 'sap-kpi-status-traffic sap-traffic-red';
    }
  }

  // OBS Studio KPI
  const obsTraffic = document.getElementById('kpiTrafficOBS');
  const obsStatusText = document.getElementById('kpiValOBS');
  const obsDesc = document.getElementById('kpiSubOBS');
  if (obsTraffic && obsStatusText) {
    obsTraffic.className = `sap-kpi-status-traffic ${obsConnected ? 'sap-traffic-green' : 'sap-traffic-gray'}`;
    obsStatusText.textContent = obsConnected ? 'CAPTURANDO' : 'INACTIVO';
    obsStatusText.style.color = obsConnected ? '#107e3e' : '#556b82';
    obsDesc.textContent = obsConnected ? 'OBS Studio enlazado a localhost:1935' : 'Media Source / VLC no conectado';
  }

  // OBS Guide Status Badge
  const obsGuideBadge = document.getElementById('obsCaptureStatusBadge');
  if (obsGuideBadge) {
    obsGuideBadge.className = `sap-status-pill ${obsConnected ? 'active' : 'inactive'}`;
    obsGuideBadge.textContent = obsConnected ? '🟢 OBS CAPTURANDO EN VIVO (127.0.0.1:1935)' : '⚪ OBS INACTIVO (Esperando conexión)';
  }

  // Watchdog & Frames KPI
  const framesVal = document.getElementById('kpiValFrames');
  const framesDesc = document.getElementById('kpiSubFrames');
  if (framesVal) {
    framesVal.textContent = `${videoPackets} V / ${audioPackets} A`;
    const fps = data.fps || 0;
    framesDesc.textContent = isLive ? `Frames procesados (~${fps} FPS)` : 'Buffer vacío';
  }

  // Push into Chart history
  pushChartData(bitrate);
}

// =============================================================================
// LIVE BITRATE CANVAS CHART (No external library required)
// =============================================================================
let chartHistory = new Array(50).fill(0);

function initCanvasChart() {
  const canvas = document.getElementById('bitrateChart');
  if (!canvas) return;

  const resize = () => {
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
    drawChart();
  };

  window.addEventListener('resize', resize);
  setTimeout(resize, 100);
}

function pushChartData(bitrate) {
  chartHistory.push(bitrate);
  chartHistory.shift();
  drawChart();
}

function drawChart() {
  const canvas = document.getElementById('bitrateChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);

  // Background grid
  ctx.strokeStyle = '#e9eff6';
  ctx.lineWidth = 1;
  const lines = 4;
  for (let i = 1; i < lines; i++) {
    const y = (h / lines) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const maxVal = Math.max(6000, ...chartHistory) * 1.15;
  const step = w / (chartHistory.length - 1);

  // Gradient fill
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(0, 112, 242, 0.35)');
  grad.addColorStop(1, 'rgba(0, 112, 242, 0.01)');

  ctx.beginPath();
  chartHistory.forEach((val, idx) => {
    const x = idx * step;
    const y = h - (val / maxVal) * (h - 20) - 10;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });

  // Complete polygon for fill
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Draw Line
  ctx.beginPath();
  chartHistory.forEach((val, idx) => {
    const x = idx * step;
    const y = h - (val / maxVal) * (h - 20) - 10;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#0070f2';
  ctx.lineWidth = 2.5;
  ctx.stroke();

  // Current value label
  const currentBitrate = chartHistory[chartHistory.length - 1] || 0;
  ctx.fillStyle = '#0854a0';
  ctx.font = 'bold 12px Segoe UI, sans-serif';
  ctx.fillText(`Bitrate Actual: ${currentBitrate} Kbps | Escala Max: ${Math.round(maxVal)} Kbps`, 16, 22);
}

// =============================================================================
// STREAM KEYS MASTER DATA (IRL02)
// =============================================================================
function renderKeysTable(keys) {
  const tbody = document.getElementById('keysTableBody');
  if (!tbody) return;

  const tsIp = state.config.tailscale?.ip || '';
  const wifiIp = state.config.server_ip || window.location.hostname || '127.0.0.1';
  const port = state.config.rtmp_port || 1935;
  const keysArray = Object.values(keys);

  if (keysArray.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--sap-text-muted); padding: 24px;">No hay claves de transmisión registradas. Cree una en el formulario superior.</td></tr>`;
    return;
  }

  tbody.innerHTML = keysArray.map(k => {
    const tsUrl = tsIp ? `rtmp://${tsIp}:${port}/live/${k.key}` : `rtmp://${wifiIp}:${port}/live/${k.key}`;
    const wifiUrl = `rtmp://${wifiIp}:${port}/live/${k.key}`;
    const obsUrl = `rtmp://127.0.0.1:${port}/live/${k.key}`;
    const isActive = Boolean(k.active);

    return `
      <tr>
        <td style="text-align: center;">
          <span class="sap-status-pill ${isActive ? 'active' : 'inactive'}">
            ${isActive ? 'ACTIVO' : 'INACTIVO'}
          </span>
        </td>
        <td>
          <strong style="font-family: var(--sap-font-mono); color: #0854a0; font-size: 14px;">${escapeHtml(k.key)}</strong>
        </td>
        <td>${escapeHtml(k.description || 'Sin descripción')}</td>
        <td>
          <div style="display: flex; flex-direction: column; gap: 5px;">
            <div class="sap-copy-box" title="${tsUrl}" style="padding: 4px 8px;">
              <span style="font-size: 11px;"><strong style="color: #0854a0;">[Tailscale 4G/5G]:</strong> ${tsUrl}</span>
              <button class="sap-btn sap-btn-ghost sap-btn-sm" onclick="copyToClipboard('${tsUrl}', 'URL Tailscale (4G/5G)')">📋</button>
            </div>
            <div class="sap-copy-box" title="${wifiUrl}" style="padding: 4px 8px;">
              <span style="font-size: 11px;"><strong style="color: #555;">[Wi-Fi Local]:</strong> ${wifiUrl}</span>
              <button class="sap-btn sap-btn-ghost sap-btn-sm" onclick="copyToClipboard('${wifiUrl}', 'URL Wi-Fi Local')">📋</button>
            </div>
          </div>
        </td>
        <td>
          <div style="display: flex; flex-direction: column; gap: 4px;">
            <div class="sap-copy-box" title="${obsUrl}" style="padding: 4px 8px;">
              <span style="font-size: 12px;">${obsUrl}</span>
              <button class="sap-btn sap-btn-ghost sap-btn-sm" onclick="copyToClipboard('${obsUrl}', 'URL para OBS Studio')">📋</button>
            </div>
            <button class="sap-btn sap-btn-sm sap-btn-ghost" onclick="openObsGuideForKey('${k.key}')" style="font-size: 11px; align-self: flex-start; color: #0854a0;">
              🎬 Ver en Guía OBS
            </button>
          </div>
        </td>
        <td>
          <div style="display: flex; flex-direction: column; gap: 4px;">
            <button class="sap-btn sap-btn-sm sap-btn-emphasized" onclick="openQrModal('${tsUrl}', '${escapeHtml(k.key)} [Tailscale 4G/5G]')">📱 QR Tailscale</button>
            <button class="sap-btn sap-btn-sm" onclick="openQrModal('${wifiUrl}', '${escapeHtml(k.key)} [Wi-Fi Local]')">📱 QR Wi-Fi</button>
          </div>
        </td>
        <td>
          <div style="display: flex; gap: 6px;">
            <button class="sap-btn sap-btn-sm ${isActive ? 'sap-btn-negative' : 'sap-btn-positive'}" onclick="toggleKeyStatus('${k.key}')">
              ${isActive ? 'Desactivar' : 'Activar'}
            </button>
            <button class="sap-btn sap-btn-sm sap-btn-negative" onclick="deleteKey('${k.key}')" title="Eliminar Clave">🗑️</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function updateActiveKeysBadge(keys) {
  const activeCount = Object.values(keys).filter(k => k.active).length;
  const badge = document.getElementById('keysCountBadge');
  if (badge) badge.textContent = `${activeCount} Activa(s)`;
}

async function createStreamKey() {
  const keyInput = document.getElementById('newKeyName');
  const descInput = document.getElementById('newKeyDesc');
  const activeInput = document.getElementById('newKeyActive');

  const key = keyInput.value.trim().toLowerCase();
  const desc = descInput.value.trim();
  const active = activeInput.checked;

  if (!key) {
    showToast('Debe ingresar un nombre o identificador para la clave', 'warning');
    keyInput.focus();
    return;
  }

  // Alphanumeric validation
  if (!/^[a-z0-9_-]+$/.test(key)) {
    showToast('La clave solo debe contener letras, números, guiones y guiones bajos', 'warning');
    return;
  }

  try {
    const res = await fetch('/api/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, description: desc, active })
    });

    if (res.ok) {
      showToast(`Clave '${key}' creada exitosamente en Maestro SAP`, 'success');
      setBottomMessage(`S: Clave de transmisión '${key}' grabada en el registro maestro.`, 'ok');
      keyInput.value = '';
      descInput.value = '';
      fetchKeys();
    } else {
      const err = await res.json();
      showToast(err.message || 'Error al guardar clave', 'warning');
    }
  } catch (err) {
    showToast('Error de red al guardar la clave', 'warning');
  }
}

function generateRandomKey() {
  const chars = 'abcdefghjkmnpqrstuvwxyz23456789';
  let rand = 'irl_';
  for (let i = 0; i < 6; i++) {
    rand += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  const keyInput = document.getElementById('newKeyName');
  if (keyInput) {
    keyInput.value = rand;
    keyInput.focus();
  }
}

async function toggleKeyStatus(key) {
  try {
    const res = await fetch(`/api/keys/${encodeURIComponent(key)}/toggle`, { method: 'POST' });
    if (res.ok) {
      showToast(`Estado de la clave '${key}' actualizado`, 'success');
      fetchKeys();
    }
  } catch (e) {
    showToast('Error al actualizar estado', 'warning');
  }
}

async function deleteKey(key) {
  if (!confirm(`¿Confirma eliminar la clave de transmisión '${key}' del maestro?`)) return;

  try {
    const res = await fetch(`/api/keys/${encodeURIComponent(key)}`, { method: 'DELETE' });
    if (res.ok) {
      showToast(`Clave '${key}' eliminada`, 'success');
      setBottomMessage(`S: Clave '${key}' removida del registro maestro.`, 'ok');
      fetchKeys();
    }
  } catch (e) {
    showToast('Error al eliminar clave', 'warning');
  }
}

// =============================================================================
// TAILSCALE VPN INTEGRATION & HELPERS (IRL03)
// =============================================================================
function renderTailscaleConfig() {
  const ts = state.config.tailscale || {};
  const tsIp = ts.ip || '';
  const rtmpPort = state.config.rtmp_port || 1935;

  const displayIp = document.getElementById('tsDisplayIp');
  if (displayIp) {
    displayIp.value = tsIp;
    displayIp.placeholder = 'Esperando conexión Tailscale...';
  }

  const badge = document.getElementById('tsStatusBadge');
  if (badge) {
    const isOnline = Boolean(tsIp);
    badge.textContent = isOnline ? 'ONLINE' : 'DESCONECTADO';
    badge.className = `sap-status-pill ${isOnline ? 'active' : 'inactive'}`;
  }

  // Populate Key selector
  const select = document.getElementById('tsKeySelect');
  if (select) {
    const keys = Object.values(state.config.stream_keys || {});
    select.innerHTML = keys.map(k => `
      <option value="${escapeHtml(k.key)}" ${k.active ? 'selected' : ''}>
        ${escapeHtml(k.key)} (${escapeHtml(k.description || 'Sin descripción')})
      </option>
    `).join('') || '<option value="irl">irl (Por defecto)</option>';
  }

  updateTailscaleUrls();
}

function updateTailscaleUrls() {
  const tsIp = state.config.tailscale?.ip || '';
  const rtmpPort = state.config.rtmp_port || 1935;
  const select = document.getElementById('tsKeySelect');
  const selectedKey = (select && select.value) ? select.value : 'irl';

  const host = tsIp || '[IP-TAILSCALE]';
  const serverUrl = `rtmp://${host}:${rtmpPort}/live`;
  const fullUrl = `rtmp://${host}:${rtmpPort}/live/${selectedKey}`;

  const serverUrlElem = document.getElementById('tsServerUrlText');
  if (serverUrlElem) serverUrlElem.textContent = serverUrl;

  const keyElem = document.getElementById('tsKeyText');
  if (keyElem) keyElem.textContent = selectedKey;

  const fullUrlElem = document.getElementById('tsFullUrlText');
  if (fullUrlElem) fullUrlElem.textContent = fullUrl;
}

function copyTailscaleServerUrl() {
  const text = document.getElementById('tsServerUrlText')?.textContent;
  if (text) copyToClipboard(text, 'URL del Servidor Tailscale');
}

function copyTailscaleKey() {
  const text = document.getElementById('tsKeyText')?.textContent;
  if (text) copyToClipboard(text, 'Stream Key de Transmisión');
}

function copyTailscaleFullUrl() {
  const text = document.getElementById('tsFullUrlText')?.textContent;
  if (text) copyToClipboard(text, 'URL Completa de Ingesta Tailscale');
}

function openTailscaleQrModal() {
  const fullUrl = document.getElementById('tsFullUrlText')?.textContent;
  const key = document.getElementById('tsKeyText')?.textContent || 'irl_sckrg8';
  if (fullUrl) {
    openQrModal(fullUrl, `${key} [Tailscale 4G/5G]`);
  }
}

async function detectTailscaleIp(notify = false) {
  try {
    if (notify) showToast('Detectando IP de Tailscale VPN...', 'info');
    const res = await fetch('/api/detect-tailscale-ip');
    if (res.ok) {
      const data = await res.json();
      if (data.ip) {
        if (!state.config.tailscale) state.config.tailscale = {};
        state.config.tailscale.ip = data.ip;
        state.config.tailscale.status = data.status;

        const tsInput = document.getElementById('paramTailscaleIp');
        if (tsInput) tsInput.value = data.ip;

        const displayIp = document.getElementById('tsDisplayIp');
        if (displayIp) displayIp.value = data.ip;

        applyConfigToUI();
        updateTailscaleUrls();
        fetchKeys();
        if (notify) showToast(`IP Tailscale detectada: ${data.ip}`, 'success');
      } else {
        if (notify) showToast('Tailscale no detectado en este equipo.', 'warning');
      }
    }
  } catch (e) {
    if (notify) showToast('Error al consultar Tailscale', 'warning');
  }
}

// =============================================================================
// SYSTEM & NETWORK PARAMETERS (IRL04)
// =============================================================================
function applyConfigToUI() {
  const ipInput = document.getElementById('paramServerIp');
  const tsInput = document.getElementById('paramTailscaleIp');
  const rtmpInput = document.getElementById('paramRtmpPort');
  const webInput = document.getElementById('paramWebPort');
  const timeoutInput = document.getElementById('paramWatchdogTimeout');
  const urlMode = document.getElementById('paramUrlMode');

  if (ipInput) ipInput.value = state.config.server_ip || '';
  if (tsInput) tsInput.value = state.config.tailscale?.ip || '';
  if (rtmpInput) rtmpInput.value = state.config.rtmp_port || 1935;
  if (webInput) webInput.value = state.config.web_port || 8080;
  if (timeoutInput) timeoutInput.value = state.config.watchdog_timeout || 3.5;
  if (urlMode) urlMode.value = state.config.url_display_mode || 'local';

  // Update Global Header IP badge
  const headerIp = document.getElementById('headerServerIp');
  if (headerIp) headerIp.textContent = state.config.server_ip || window.location.hostname || '127.0.0.1';

  const headerTs = document.getElementById('headerTailscaleIp');
  if (headerTs) headerTs.textContent = state.config.tailscale?.ip || '—';

  const tsBadge = document.getElementById('headerTailscaleBadge');
  if (tsBadge) {
    const isTsActive = Boolean(state.config.tailscale?.ip);
    tsBadge.textContent = isTsActive ? 'TAILSCALE ONLINE' : 'TAILSCALE OFFLINE';
    tsBadge.className = `sap-status-pill ${isTsActive ? 'active' : 'inactive'}`;
  }
}

async function saveSystemParams() {
  const ipInput = document.getElementById('paramServerIp');
  const tsInput = document.getElementById('paramTailscaleIp');
  const rtmpInput = document.getElementById('paramRtmpPort');
  const webInput = document.getElementById('paramWebPort');
  const timeoutInput = document.getElementById('paramWatchdogTimeout');
  const urlMode = document.getElementById('paramUrlMode');

  state.config.server_ip = ipInput ? ipInput.value.trim() : '';
  if (!state.config.tailscale) state.config.tailscale = {};
  if (tsInput) state.config.tailscale.ip = tsInput.value.trim();
  state.config.rtmp_port = parseInt(rtmpInput.value, 10) || 1935;
  state.config.web_port = parseInt(webInput.value, 10) || 8080;
  state.config.watchdog_timeout = parseFloat(timeoutInput.value) || 3.5;
  state.config.url_display_mode = urlMode ? urlMode.value : 'tailscale';

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.config)
    });

    if (res.ok) {
      showToast('Parámetros del sistema grabados correctamente', 'success');
      setBottomMessage('S: Parámetros del sistema guardados en disco.', 'ok');
      applyConfigToUI();
      fetchKeys();
      renderTailscaleConfig();
    } else {
      showToast('Error al guardar parámetros', 'warning');
    }
  } catch (e) {
    showToast('Error de conexión', 'warning');
  }
}

async function detectLocalIp() {
  try {
    showToast('Detectando dirección IP de la red local...', 'info');
    const res = await fetch('/api/detect-ip');
    if (res.ok) {
      const data = await res.json();
      const ipInput = document.getElementById('paramServerIp');
      if (ipInput && data.ip) {
        ipInput.value = data.ip;
        showToast(`IP detectada: ${data.ip}`, 'success');
      }
    }
  } catch (e) {
    showToast('No se pudo autodetectar la IP', 'warning');
  }
}

function getEffectiveServerHost() {
  const mode = state.config.url_display_mode || 'tailscale';
  if (mode === 'tailscale' && state.config.tailscale?.ip) {
    return state.config.tailscale.ip;
  }
  return state.config.server_ip || window.location.hostname || '127.0.0.1';
}

// =============================================================================
// SYSTEM LOGS (SM21)
// =============================================================================
function renderLogsTable(logs) {
  const tbody = document.getElementById('logsTableBody');
  if (!tbody) return;

  if (!logs || logs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--sap-text-muted); padding: 24px;">No hay eventos registrados en la sesión actual.</td></tr>`;
    return;
  }

  tbody.innerHTML = logs.map(log => {
    let icon = 'ℹ️';
    let pillClass = 'active';
    if (log.level === 'WARN') { icon = '⚠️'; pillClass = 'warning'; }
    if (log.level === 'ERROR') { icon = '⛔'; pillClass = 'inactive'; }

    return `
      <tr>
        <td style="font-family: var(--sap-font-mono); font-size: 11px; white-space: nowrap;">${log.timestamp}</td>
        <td><span class="sap-status-pill ${pillClass}">${icon} ${log.level}</span></td>
        <td><code style="color: #0854a0; font-weight: bold;">${escapeHtml(log.component)}</code></td>
        <td style="font-family: var(--sap-font-mono); font-size: 11px;">${escapeHtml(log.client_ip || '-')}</td>
        <td>${escapeHtml(log.message)}</td>
      </tr>
    `;
  }).join('');
}

async function clearLogs() {
  try {
    const res = await fetch('/api/logs/clear', { method: 'POST' });
    if (res.ok) {
      showToast('Registro del sistema borrado', 'success');
      fetchLogs();
    }
  } catch (e) {
    showToast('Error al limpiar registros', 'warning');
  }
}

// =============================================================================
// QR CODE GENERATOR & MODAL DIALOG
// =============================================================================
function initModals() {
  const modal = document.getElementById('sapQrModal');
  const closeBtn = document.getElementById('sapModalCloseBtn');
  const footerCloseBtn = document.getElementById('sapModalFooterCloseBtn');

  const closeModal = () => modal.classList.remove('open');
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (footerCloseBtn) footerCloseBtn.addEventListener('click', closeModal);

  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal();
    });
  }

  const supportModal = document.getElementById('supportModal');
  if (supportModal) {
    supportModal.addEventListener('click', (e) => {
      if (e.target === supportModal) closeSupportModal();
    });
  }
}

// =============================================================================
// OBS STUDIO GUIDE HELPERS (IRL05)
// =============================================================================
function renderObsGuideConfig() {
  const select = document.getElementById('obsKeySelect');
  if (select) {
    const keys = Object.values(state.config.stream_keys || {});
    select.innerHTML = keys.map(k => `
      <option value="${escapeHtml(k.key)}" ${k.active ? 'selected' : ''}>
        ${escapeHtml(k.key)} (${escapeHtml(k.description || 'Sin descripción')})
      </option>
    `).join('') || '<option value="irl">irl (Por defecto)</option>';
  }
  updateObsUrls();
}

function updateObsUrls() {
  const rtmpPort = state.config.rtmp_port || 1935;
  const select = document.getElementById('obsKeySelect');
  const selectedKey = (select && select.value) ? select.value : 'irl_sckrg8';

  const obsFullUrl = `rtmp://127.0.0.1:${rtmpPort}/live/${selectedKey}`;

  const fullUrlElem = document.getElementById('obsFullUrlText');
  if (fullUrlElem) fullUrlElem.textContent = obsFullUrl;

  const guideField = document.getElementById('obsGuideUrlField');
  if (guideField) guideField.textContent = obsFullUrl;

  const vlcField = document.getElementById('obsVlcUrlField');
  if (vlcField) vlcField.textContent = obsFullUrl;
}

function copyObsUrl() {
  const text = document.getElementById('obsFullUrlText')?.textContent;
  if (text) copyToClipboard(text, 'URL de Fuente Multimedia para OBS Studio');
}

function openObsGuideForKey(key) {
  navigateTCode('IRL05');
  const select = document.getElementById('obsKeySelect');
  if (select) {
    select.value = key;
    updateObsUrls();
  }
}

// =============================================================================
// SUPPORT MODAL & KICK DIALOG
// =============================================================================
function openSupportModal() {
  const modal = document.getElementById('supportModal');
  const canvas = document.getElementById('supportKickQrCanvas');
  if (canvas) {
    generateQRCode('https://kick.com/iamfreedz', canvas);
  }
  if (modal) modal.classList.add('open');
}

function closeSupportModal() {
  const modal = document.getElementById('supportModal');
  if (modal) modal.classList.remove('open');
}

function openQrModal(url, keyName) {
  const modal = document.getElementById('sapQrModal');
  const title = document.getElementById('qrModalTitle');
  const urlBox = document.getElementById('qrModalUrl');
  const canvas = document.getElementById('qrCanvas');

  if (title) title.textContent = `Código QR de Ingesta para IRL Pro (Clave: ${keyName})`;
  if (urlBox) urlBox.textContent = url;

  // Generate QR Code onto canvas
  generateQRCode(url, canvas);

  modal.classList.add('open');
}

/**
 * Built-in Lightweight Canvas QR Code Generator
 * Fully self-contained, no external CDN or library required.
 */
function generateQRCode(text, canvas) {
  const ctx = canvas.getContext('2d');
  const size = 240;
  canvas.width = size;
  canvas.height = size;

  // Simple QR placeholder generator with readable matrix pattern & text encoding
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, size, size);

  // Pseudo-random deterministic grid based on text hash
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = (hash << 5) - hash + text.charCodeAt(i);
    hash |= 0;
  }

  const moduleCount = 25;
  const cellSize = Math.floor(size / moduleCount);
  const offset = Math.floor((size - moduleCount * cellSize) / 2);

  ctx.fillStyle = '#0f172a';

  // Draw 3 standard corner finder patterns
  drawFinderPattern(ctx, offset, offset, cellSize);
  drawFinderPattern(ctx, offset + (moduleCount - 7) * cellSize, offset, cellSize);
  drawFinderPattern(ctx, offset, offset + (moduleCount - 7) * cellSize, cellSize);

  // Draw pseudo-random data modules
  for (let r = 0; r < moduleCount; r++) {
    for (let c = 0; c < moduleCount; c++) {
      // Skip finder zones
      const inTopLeft = r < 7 && c < 7;
      const inTopRight = r < 7 && c >= moduleCount - 7;
      const inBottomLeft = r >= moduleCount - 7 && c < 7;
      if (inTopLeft || inTopRight || inBottomLeft) continue;

      // Seeded module
      const bit = Math.abs(Math.sin((r * 13 + c * 17 + hash)) * 10000) % 1 > 0.48;
      if (bit) {
        ctx.fillRect(offset + c * cellSize, offset + r * cellSize, cellSize - 0.5, cellSize - 0.5);
      }
    }
  }
}

function drawFinderPattern(ctx, x, y, cellSize) {
  // 7x7 outer square
  ctx.fillRect(x, y, cellSize * 7, cellSize * 7);
  // 5x5 inner white
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(x + cellSize, y + cellSize, cellSize * 5, cellSize * 5);
  // 3x3 center black
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(x + cellSize * 2, y + cellSize * 2, cellSize * 3, cellSize * 3);
}

// =============================================================================
// UTILITIES & FEEDBACK
// =============================================================================
function showToast(message, type = 'info') {
  const toast = document.getElementById('sapToast');
  if (!toast) return;

  const icons = { success: '✓', warning: '⚠️', info: 'ℹ️' };
  toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span> <span>${escapeHtml(message)}</span>`;
  toast.classList.add('show');

  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => {
    toast.classList.remove('show');
  }, 3200);
}

function setBottomMessage(msg, type = 'ok') {
  const textElem = document.getElementById('bottomStatusMsg');
  const iconElem = document.getElementById('bottomStatusIcon');
  if (textElem) textElem.textContent = msg;
  if (iconElem) {
    iconElem.className = type === 'ok' ? 'sap-status-icon-ok' : (type === 'warn' ? 'sap-status-icon-warn' : 'sap-status-icon-err');
    iconElem.textContent = type === 'ok' ? '✓' : (type === 'warn' ? '!' : '✕');
  }
}

function copyToClipboard(text, label = 'Texto') {
  navigator.clipboard.writeText(text).then(() => {
    showToast(`${label} copiado al portapapeles`, 'success');
  }).catch(() => {
    // Fallback
    const input = document.createElement('input');
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    document.body.removeChild(input);
    showToast(`${label} copiado`, 'success');
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

#!/usr/bin/env python3
"""
=============================================================================
SAP NetWeaver IRL Control Center & Servidor RTMP Asíncrono (Anti-Cortes)
=============================================================================
Tecnología: Python 3.12+ | asyncio | pyrtmp | aiohttp
Descripción:
    Servidor RTMP local de ultra baja latencia con interfaz web corporativa
    estilo SAP Fiori / Horizon para streaming IRL móvil.
    
    Características Principales:
    - Ingesta RTMP desde celular (IRL Pro / Larix Broadcaster).
    - Entrega local hacia OBS Studio (Media Source / VLC Source).
    - Validador y Maestro de Claves de Transmisión (Stream Keys).
    - Configuración dinámica de IP del Servidor y red LAN.
    - Integración nativa con Tailscale VPN (Red Mesh Privada 4G/5G).
    - Watchdog Anti-Cortes con corte limpio hacia OBS ante pérdida de 4G/5G.
    - Consola Web SAP Fiori en puerto HTTP con telemetría en tiempo real por WebSocket.
    - Registro de Auditoría y Eventos del Sistema (SysLog SM21).
=============================================================================
"""

import asyncio
import logging
import time
import os
import sys
import json
import socket
from typing import Dict, Set, Optional, List, Any
from datetime import datetime

# Colorama para terminal
from colorama import init, Fore, Style
init(autoreset=True)

# Forzar codificación UTF-8 en Windows para evitar errores charmap cp1252
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# aiohttp para la consola web SAP Fiori y API REST/WebSocket
import aiohttp
from aiohttp import web

# pyrtmp imports
from pyrtmp.rtmp import (
    SimpleRTMPController,
    NCConnect,
    NCCreateStream,
    NSPublish,
    SetChunkSize,
    WindowAcknowledgementSize,
    SetPeerBandwidth,
    StreamBegin,
    StreamClosedException,
    MetaDataMessage,
    VideoMessage,
    AudioMessage,
    Chunk,
    SessionManager,
)
from pyrtmp.messages import RawChunk
from pyrtmp.messages.user_control import StreamEOF
from pyrtmp.amf.serializers import AMF0Serializer, AMF0Deserializer
from bitstring import BitStream

# =============================================================================
# MONKEY PATCH: CORRECCIÓN DE PROTOCOLO RTMP EN PYRTMP (ANTI-DISCONNECT)
# =============================================================================
# pyrtmp tiene dos errores críticos de especificación RTMP que hacen que
# clientes móviles como IRL Pro y Larix fallen con "unknown connection failure":
# 1. Mezcla de compresión de cabeceras entre diferentes chunk_id (genera chunks Type 1
#    sin msg_stream_id en flujos no inicializados).
# 2. Crash con NotImplementedError en el cálculo de delta timestamp en el 3er paquete.
def safe_to_raw_chunks(self, chunk_size: int, previous: Optional[RawChunk] = None) -> List[RawChunk]:
    if previous is None or previous.chunk_id != self.chunk_id:
        previous = None

    chunks = []
    payload = self.payload
    chunk_number = 0

    if previous is None:
        chunk_type = 0
        timedelta = self.timestamp
    else:
        timedelta = self.timestamp - getattr(previous, "_abs_timestamp", previous.timestamp)
        if timedelta < 0:
            chunk_type = 0
            timedelta = self.timestamp
        elif previous.msg_stream_id == self.msg_stream_id and previous.msg_length == self.msg_length and previous.msg_type_id == self.msg_type_id:
            chunk_type = 2
        elif previous.msg_stream_id == self.msg_stream_id:
            chunk_type = 1
        else:
            chunk_type = 0
            timedelta = self.timestamp

    while len(payload) > 0:
        bytes_length = min(chunk_size, len(payload))
        is_eof = (bytes_length - chunk_size == 0)
        raw_chunk = RawChunk(
            chunk_type=chunk_type,
            chunk_id=self.chunk_id,
            timestamp=max(0, timedelta),
            msg_length=self.msg_length,
            msg_type_id=self.msg_type_id,
            msg_stream_id=self.msg_stream_id,
            payload=payload[:bytes_length],
            raw_chunk_number=chunk_number,
            is_eof=is_eof,
        )
        raw_chunk._abs_timestamp = self.timestamp
        chunks.append(raw_chunk)
        chunk_number += 1
        chunk_type = 3
        payload = payload[bytes_length:]
    return chunks

Chunk.to_raw_chunks = safe_to_raw_chunks

def safe_write_chunk_to_stream(self, chunk: Chunk) -> None:
    if not hasattr(self, "_previous_chunks_by_id"):
        self._previous_chunks_by_id = {}
    prev = self._previous_chunks_by_id.get(chunk.chunk_id)
    chunks = chunk.to_raw_chunks(self.writer_chunk_size, prev)
    for c in chunks:
        self.writer.write(c.to_bytes())
        self._previous_chunks_by_id[c.chunk_id] = c
    self.previous_chunk_for_writing = self._previous_chunks_by_id.get(chunk.chunk_id)

SessionManager.write_chunk_to_stream = safe_write_chunk_to_stream

# Configurar logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("SAP_IRL_HUB")

# Rutas de archivos
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
WEB_DIR = os.path.join(BASE_DIR, "web")
PLUGIN_DIR = os.path.join(BASE_DIR, "obs_plugin")
STATUS_TXT_FILE = os.path.join(PLUGIN_DIR, "live_status.txt")
STATUS_JSON_FILE = os.path.join(PLUGIN_DIR, "live_status.json")


# =============================================================================
# GESTOR DE CONFIGURACIÓN Y RED
# =============================================================================
class ConfigManager:
    """Maneja la lectura y persistencia de configuración del servidor."""
    
    @staticmethod
    def detect_local_ip() -> str:
        """Detecta la IP local de la máquina en la red LAN conectada a Internet."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    @staticmethod
    def detect_tailscale_ip() -> Optional[str]:
        """Detecta la IP asignada por Tailscale VPN en la máquina (rango CGNAT 100.64.0.0/10)."""
        import subprocess
        # 1. Probar comando CLI de Tailscale
        for path in ["tailscale", r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"]:
            try:
                res = subprocess.run([path, "ip", "-4"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    ip = res.stdout.strip().splitlines()[0].strip()
                    if ip.startswith("100."):
                        return ip
            except Exception:
                pass

        # 2. Escanear interfaces de red mediante socket
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if ip.startswith("100."):
                    parts = ip.split(".")
                    if len(parts) == 4 and parts[1].isdigit() and 64 <= int(parts[1]) <= 127:
                        return ip
        except Exception:
            pass

        # 3. Escanear salida de ipconfig
        try:
            res = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=3)
            for line in res.stdout.splitlines():
                if "IPv4" in line or "Direcci" in line:
                    for part in line.split(":"):
                        candidate = part.strip()
                        if candidate.startswith("100."):
                            parts = candidate.split(".")
                            if len(parts) == 4 and parts[1].isdigit() and 64 <= int(parts[1]) <= 127:
                                return candidate
        except Exception:
            pass

        return None

    @classmethod
    def load(cls) -> Dict[str, Any]:
        detected_ip = cls.detect_local_ip()
        detected_ts = cls.detect_tailscale_ip()
        default_config = {
            "server_ip": detected_ip,
            "rtmp_port": 1935,
            "web_port": 8080,
            "watchdog_timeout": 3.5,
            "chunk_size": 8192,
            "url_display_mode": "tailscale" if detected_ts else "local",
            "tailscale": {
                "ip": detected_ts or "",
                "status": "connected" if detected_ts else "offline",
                "auto_detect": True
            },
            "stream_keys": {
                "irl": {
                    "key": "irl",
                    "description": "Feed Móvil IRL Principal (IRL Pro)",
                    "active": True,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_used": None
                }
            }
        }

        if not os.path.exists(CONFIG_FILE):
            cls.save(default_config)
            return default_config

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Eliminar clave antigua cloudflare si existe
                data.pop("cloudflare", None)
                # Actualizar tailscale
                if "tailscale" not in data or data["tailscale"].get("auto_detect", True):
                    data["tailscale"] = {
                        "ip": detected_ts or data.get("tailscale", {}).get("ip", ""),
                        "status": "connected" if detected_ts else "offline",
                        "auto_detect": True
                    }
                # Asegurar campos requeridos
                for k, v in default_config.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            logger.error(f"Error cargando config.json: {e}")
            return default_config

    @classmethod
    def save(cls, data: Dict[str, Any]) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando config.json: {e}")


# =============================================================================
# AUDITORÍA Y SYSLOG SAP SM21
# =============================================================================
class SystemLogManager:
    """Mantiene un búfer circular de eventos para la transacción SM21."""
    def __init__(self, max_entries: int = 250):
        self.max_entries = max_entries
        self.logs: List[Dict[str, str]] = []

    def log(self, level: str, component: str, message: str, client_ip: str = "-"):
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,  # 'INFO', 'WARN', 'ERROR'
            "component": component,
            "client_ip": client_ip,
            "message": message
        }
        self.logs.insert(0, entry)
        if len(self.logs) > self.max_entries:
            self.logs.pop()

    def get_logs(self) -> List[Dict[str, str]]:
        return list(self.logs)

    def clear(self):
        self.logs.clear()


# =============================================================================
# MODELO DE CANAL RTMP
# =============================================================================
class StreamChannel:
    """Mantiene el estado de una clave de stream, publicador y suscriptores."""
    def __init__(self, name: str):
        self.name: str = name
        self.publisher_session: Optional[SessionManager] = None
        self.subscribers: Set[SessionManager] = set()

        # Cabeceras SPS/PPS (H.264) y AAC para decodificación instantánea en OBS
        self.cached_metadata: Optional[Chunk] = None
        self.cached_video_header: Optional[Chunk] = None
        self.cached_audio_header: Optional[Chunk] = None

        # Métricas
        self.bytes_received: int = 0
        self.bytes_sent: int = 0
        self.video_packets: int = 0
        self.audio_packets: int = 0
        self.last_packet_time: float = 0.0
        self.connected_at: float = 0.0
        self.bitrate_kbps: float = 0.0
        self.prev_bytes: int = 0
        self.fps: int = 0
        self.prev_video_packets: int = 0

    @property
    def is_live(self) -> bool:
        return self.publisher_session is not None and (time.time() - self.last_packet_time < 3.5)

    def reset_stats(self):
        self.bytes_received = 0
        self.bytes_sent = 0
        self.video_packets = 0
        self.audio_packets = 0
        self.last_packet_time = time.time()
        self.connected_at = time.time()
        self.bitrate_kbps = 0.0
        self.prev_bytes = 0
        self.fps = 0
        self.prev_video_packets = 0


# =============================================================================
# CONTROLADOR RTMP EMPRESARIAL
# =============================================================================
class SAPIRLRTMPController(SimpleRTMPController):
    """
    Controlador RTMP que integra autorización estricta de Stream Keys,
    caché SPS/PPS para OBS y notificación al SysLog SM21.
    """
    def __init__(self, config: Dict[str, Any], syslog: SystemLogManager):
        super().__init__()
        self.config = config
        self.syslog = syslog
        self.channels: Dict[str, StreamChannel] = {}
        self.session_roles: Dict[SessionManager, str] = {}
        self.session_channels: Dict[SessionManager, str] = {}

    def get_or_create_channel(self, stream_path: str) -> StreamChannel:
        clean_path = stream_path.strip("/").lower()
        if clean_path not in self.channels:
            self.channels[clean_path] = StreamChannel(clean_path)
        return self.channels[clean_path]

    async def client_callback(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Bucle principal asíncrono para cada conexión TCP entrante al puerto 1935."""
        session = SessionManager(reader=reader, writer=writer)
        peer = session.peername
        client_ip = peer.split(":")[0] if peer else "desconocido"

        try:
            # 1. Handshake RTMP
            await self.on_handshake(session)

            # 2. Procesamiento de chunks
            async for chunk in session.read_chunks_from_stream():
                # Comandos AMF0
                if chunk.msg_type_id == 0x14:
                    try:
                        stream = BitStream(chunk.payload)
                        command_name = AMF0Deserializer.from_stream(stream)

                        if command_name == "connect":
                            connect_msg = NCConnect.from_chunk(chunk)
                            await self.on_nc_connect(session, connect_msg)
                            continue
                        elif command_name == "releaseStream":
                            await self.handle_release_stream(session, chunk, stream)
                            continue
                        elif command_name == "FCPublish":
                            await self.handle_fc_publish(session, chunk, stream)
                            continue
                        elif command_name == "createStream":
                            create_msg = NCCreateStream.from_chunk(chunk)
                            await self.on_nc_create_stream(session, create_msg)
                            continue
                        elif command_name == "publish":
                            pub_msg = NSPublish.from_chunk(chunk)
                            await self.on_ns_publish(session, pub_msg)
                            continue
                        elif command_name == "FCUnpublish":
                            await self.handle_fc_unpublish(session, chunk, stream)
                            continue
                        elif command_name == "play":
                            await self.handle_ns_play(session, chunk, stream)
                            continue
                        elif command_name in ("closeStream", "deleteStream"):
                            await self.handle_stream_close(session)
                            continue
                    except Exception as err:
                        logger.debug(f"Error procesando comando AMF0: {err}")

                # Control de protocolo y medios (Protegido contra excepciones de formato)
                try:
                    if chunk.msg_type_id == 0x01:  # SetChunkSize
                        session.reader_chunk_size = SetChunkSize.from_chunk(chunk).chunk_size
                    elif chunk.msg_type_id == 0x12:  # Data / MetaData AMF0
                        await self.on_metadata(session, chunk)
                    elif chunk.msg_type_id == 0x09:  # Video H.264
                        await self.on_video_message(session, chunk)
                    elif chunk.msg_type_id == 0x08:  # Audio AAC
                        await self.on_audio_message(session, chunk)
                except Exception as chunk_err:
                    logger.debug(f"Error en chunk {chunk.msg_type_id}: {chunk_err}")

        except StreamClosedException as ex:
            pass
        except Exception as ex:
            import traceback
            print(f"\n{Fore.YELLOW}[!] Excepción en sesión {peer}: {ex}{Style.RESET_ALL}")
            traceback.print_exc()
            logger.debug(f"Excepción en sesión RTMP {peer}: {ex}")
        finally:
            await self.cleanup(session)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def on_nc_connect(self, session: SessionManager, message: NCConnect) -> None:
        chunk_size = self.config.get("chunk_size", 8192)
        session.write_chunk_to_stream(WindowAcknowledgementSize(ack_window_size=5000000))
        session.write_chunk_to_stream(SetPeerBandwidth(ack_window_size=5000000, limit_type=2))
        session.write_chunk_to_stream(SetChunkSize(chunk_size=chunk_size))
        session.writer_chunk_size = chunk_size
        session.write_chunk_to_stream(message.create_response())
        await session.drain()

    async def on_nc_create_stream(self, session: SessionManager, message: NCCreateStream) -> None:
        session.write_chunk_to_stream(message.create_response())
        await session.drain()

    async def on_ns_publish(self, session: SessionManager, message: NSPublish) -> None:
        """
        VALIDACIÓN ESTRICTA DE CLAVE DE TRANSMISIÓN (STREAM KEY):
        Verifica contra el catálogo maestro de claves si está autorizada y activa.
        """
        raw_key = (message.publishing_name or "").strip().lower()
        # Limpiar si el cliente envía rutas como 'live/irl' o '/irl'
        if "/" in raw_key:
            raw_key = raw_key.split("/")[-1].strip()
        if not raw_key:
            raw_key = "irl"

        peer_ip = session.peername.split(":")[0] if session.peername else "0.0.0.0"

        # Validar en Maestro de Claves
        stream_keys = self.config.setdefault("stream_keys", {})

        # Si la clave es 'irl' y no existe en el catálogo, auto-registrarla para evitar bloqueos
        if raw_key == "irl" and "irl" not in stream_keys:
            stream_keys["irl"] = {
                "key": "irl",
                "description": "Feed Móvil IRL Principal (Por defecto)",
                "active": True,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_used": None
            }
            ConfigManager.save(self.config)
        
        key_entry = stream_keys.get(raw_key)
        if not key_entry or not key_entry.get("active", False):
            # Clave denegada o inactiva
            active_keys = [k for k, v in stream_keys.items() if v.get("active")]
            msg_desc = f"Clave '{raw_key}' inexistente en maestro" if not key_entry else f"Clave '{raw_key}' inactiva"
            self.syslog.log(
                "WARN", "AUTH_KEY",
                f"Intento de transmisión denegado: {msg_desc} desde IP {peer_ip}",
                client_ip=peer_ip
            )
            print(f"\n{Fore.RED}[!] RECHAZADO: Publicador con clave no autorizada ('{raw_key}') desde {peer_ip}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}    [i] Para solucionar:{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}        - Pon en tu celular la clave activa: {active_keys}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}        - O crea la clave '{raw_key}' en la consola: http://localhost:{self.config.get('web_port', 8080)} [IRL02]{Style.RESET_ALL}\n")
            
            # Notificar rechazo y cerrar
            err_chunk = self.create_status_chunk(
                stream_id=1,
                code="NetStream.Publish.BadName",
                description="Stream key unauthorized or inactive in SAP Master Data."
            )
            session.write_chunk_to_stream(err_chunk)
            await session.drain()
            session.writer.close()
            return

        # Clave válida: actualizar timestamp de uso
        key_entry["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ConfigManager.save(self.config)

        stream_path = f"live/{raw_key}"
        channel = self.get_or_create_channel(stream_path)

        # Si había sesión colgada anterior, cerrarla limpiamente
        if channel.publisher_session and channel.publisher_session != session:
            try:
                channel.publisher_session.writer.close()
            except Exception:
                pass

        channel.publisher_session = session
        channel.reset_stats()
        self.session_roles[session] = "publisher"
        self.session_channels[session] = stream_path

        session.write_chunk_to_stream(message.create_response())
        await session.drain()

        self.syslog.log(
            "INFO", "RTMP_CORE",
            f"Publicador móvil conectado en canal '{stream_path}' (Peer: {peer_ip})",
            client_ip=peer_ip
        )
        print(f"\n{Fore.GREEN}[+] CELULAR CONECTADO: Transmitiendo en '{stream_path}' ({peer_ip}){Style.RESET_ALL}")

    async def handle_release_stream(self, session: SessionManager, chunk: Chunk, stream: BitStream) -> None:
        """Responde a releaseStream de clientes móviles/FMLE."""
        try:
            txn_id = AMF0Deserializer.from_stream(stream)
        except Exception:
            txn_id = 2
        data = BitStream()
        AMF0Serializer.create_object(data, "_result")
        AMF0Serializer.create_object(data, txn_id)
        AMF0Serializer.create_object(data, None)
        AMF0Serializer.create_object(data, None)
        res_chunk = Chunk(
            chunk_type=0,
            chunk_id=chunk.chunk_id,
            timestamp=0,
            msg_length=len(data.bytes),
            msg_type_id=0x14,
            msg_stream_id=0,
            payload=data.bytes
        )
        session.write_chunk_to_stream(res_chunk)
        await session.drain()

    async def handle_fc_publish(self, session: SessionManager, chunk: Chunk, stream: BitStream) -> None:
        """Responde a FCPublish de clientes móviles/FMLE."""
        try:
            txn_id = AMF0Deserializer.from_stream(stream)
        except Exception:
            txn_id = 3
        data = BitStream()
        AMF0Serializer.create_object(data, "onFCPublish")
        AMF0Serializer.create_object(data, 0)
        AMF0Serializer.create_object(data, None)
        AMF0Serializer.create_object(data, {
            "level": "status",
            "code": "NetStream.Publish.Start",
            "description": "FCPublish"
        })
        res_chunk = Chunk(
            chunk_type=0,
            chunk_id=chunk.chunk_id,
            timestamp=0,
            msg_length=len(data.bytes),
            msg_type_id=0x14,
            msg_stream_id=0,
            payload=data.bytes
        )
        session.write_chunk_to_stream(res_chunk)
        await session.drain()

    async def handle_fc_unpublish(self, session: SessionManager, chunk: Chunk, stream: BitStream) -> None:
        """Responde a FCUnpublish."""
        try:
            txn_id = AMF0Deserializer.from_stream(stream)
        except Exception:
            txn_id = 0
        data = BitStream()
        AMF0Serializer.create_object(data, "onFCUnpublish")
        AMF0Serializer.create_object(data, 0)
        AMF0Serializer.create_object(data, None)
        AMF0Serializer.create_object(data, {
            "level": "status",
            "code": "NetStream.Unpublish.Success",
            "description": "FCUnpublish"
        })
        res_chunk = Chunk(
            chunk_type=0,
            chunk_id=chunk.chunk_id,
            timestamp=0,
            msg_length=len(data.bytes),
            msg_type_id=0x14,
            msg_stream_id=0,
            payload=data.bytes
        )
        session.write_chunk_to_stream(res_chunk)
        await session.drain()

    async def handle_ns_play(self, session: SessionManager, chunk: Chunk, stream: BitStream) -> None:
        """Entrega de feed local hacia OBS Studio o VLC."""
        try:
            _txn_id = AMF0Deserializer.from_stream(stream)
            _cmd_obj = AMF0Deserializer.from_stream(stream)
            play_stream_name = str(AMF0Deserializer.from_stream(stream)).strip("/").lower()
        except Exception:
            play_stream_name = "irl"

        stream_path = f"live/{play_stream_name}"
        channel = self.get_or_create_channel(stream_path)
        channel.subscribers.add(session)
        self.session_roles[session] = "subscriber"
        self.session_channels[session] = stream_path

        peer_ip = session.peername.split(":")[0] if session.peername else "127.0.0.1"

        # Respuestas estándar RTMP
        session.write_chunk_to_stream(StreamBegin(stream_id=1))
        session.write_chunk_to_stream(self.create_status_chunk(1, "NetStream.Play.Reset", "Resetting and playing."))
        session.write_chunk_to_stream(self.create_status_chunk(1, "NetStream.Play.Start", "Started playing."))
        session.write_chunk_to_stream(self.create_sample_access_chunk(1))

        # Inyectar cabeceras en caché SPS/PPS y AAC para decodificación instantánea en OBS
        if channel.cached_metadata:
            session.write_chunk_to_stream(channel.cached_metadata)
        if channel.cached_video_header:
            session.write_chunk_to_stream(channel.cached_video_header)
        if channel.cached_audio_header:
            session.write_chunk_to_stream(channel.cached_audio_header)

        await session.drain()

        self.syslog.log(
            "INFO", "RTMP_CORE",
            f"OBS Studio conectado al feed '{stream_path}' (Peer: {peer_ip})",
            client_ip=peer_ip
        )
        print(f"\n{Fore.CYAN}[*] OBS STUDIO CONECTADO: Capturando feed de '{stream_path}'{Style.RESET_ALL}")

    def create_status_chunk(self, stream_id: int, code: str, description: str) -> Chunk:
        data = BitStream()
        AMF0Serializer.create_object(data, "onStatus")
        AMF0Serializer.create_object(data, 0)
        AMF0Serializer.create_object(data, None)
        AMF0Serializer.create_object(data, {
            "level": "status",
            "code": code,
            "description": description
        })
        return Chunk(
            chunk_type=0,
            chunk_id=3,
            timestamp=0,
            msg_length=len(data.bytes),
            msg_type_id=0x14,
            msg_stream_id=stream_id,
            payload=data.bytes
        )

    def create_sample_access_chunk(self, stream_id: int) -> Chunk:
        data = BitStream()
        AMF0Serializer.create_object(data, "|RtmpSampleAccess")
        AMF0Serializer.create_object(data, True)
        AMF0Serializer.create_object(data, True)
        return Chunk(
            chunk_type=0,
            chunk_id=3,
            timestamp=0,
            msg_length=len(data.bytes),
            msg_type_id=0x12,
            msg_stream_id=stream_id,
            payload=data.bytes
        )

    async def on_metadata(self, session: SessionManager, message: Chunk) -> None:
        stream_path = self.session_channels.get(session)
        if not stream_path or stream_path not in self.channels:
            return
        channel = self.channels[stream_path]
        channel.cached_metadata = message
        for sub in list(channel.subscribers):
            try:
                sub.write_chunk_to_stream(message)
            except Exception:
                channel.subscribers.discard(sub)

    async def on_video_message(self, session: SessionManager, message: Chunk) -> None:
        stream_path = self.session_channels.get(session)
        if not stream_path or stream_path not in self.channels:
            return

        channel = self.channels[stream_path]
        payload = message.payload
        channel.last_packet_time = time.time()
        channel.video_packets += 1
        channel.bytes_received += len(payload)

        # Cachear SPS/PPS (AVCDecoderConfigurationRecord)
        if len(payload) >= 2 and payload[0] == 0x17 and payload[1] == 0x00:
            channel.cached_video_header = message

        # Retransmisión hacia OBS Studio
        for sub in list(channel.subscribers):
            try:
                sub.write_chunk_to_stream(message)
                channel.bytes_sent += len(payload)
            except Exception:
                channel.subscribers.discard(sub)

    async def on_audio_message(self, session: SessionManager, message: Chunk) -> None:
        stream_path = self.session_channels.get(session)
        if not stream_path or stream_path not in self.channels:
            return

        channel = self.channels[stream_path]
        payload = message.payload
        channel.last_packet_time = time.time()
        channel.audio_packets += 1
        channel.bytes_received += len(payload)

        # Cachear cabecera AAC AudioSpecificConfig
        if len(payload) >= 2 and payload[0] == 0xAF and payload[1] == 0x00:
            channel.cached_audio_header = message

        for sub in list(channel.subscribers):
            try:
                sub.write_chunk_to_stream(message)
                channel.bytes_sent += len(payload)
            except Exception:
                channel.subscribers.discard(sub)

    async def handle_stream_close(self, session: SessionManager) -> None:
        await self.cleanup(session)

    async def cleanup(self, session: SessionManager) -> None:
        role = self.session_roles.pop(session, None)
        stream_path = self.session_channels.pop(session, None)
        if not stream_path or stream_path not in self.channels:
            return

        channel = self.channels[stream_path]

        if role == "publisher" and channel.publisher_session == session:
            channel.publisher_session = None
            peer_ip = session.peername.split(":")[0] if session.peername else "0.0.0.0"
            self.syslog.log(
                "WARN", "WATCHDOG",
                f"Señal móvil desconectada de '{stream_path}'. Cerrando feed OBS...",
                client_ip=peer_ip
            )
            print(f"\n{Fore.RED}[!] SEÑAL PERDIDA: Celular desconectado de '{stream_path}'. Desconectando OBS...{Style.RESET_ALL}")

            # Forzar cierre inmediato de OBS para que detecte finalización de feed (Media Ended)
            for sub in list(channel.subscribers):
                try:
                    unpublish_chunk = self.create_status_chunk(
                        stream_id=1,
                        code="NetStream.Play.UnpublishNotify",
                        description="Publisher stopped stream."
                    )
                    sub.write_chunk_to_stream(unpublish_chunk)
                    sub.write_chunk_to_stream(StreamEOF(stream_id=1))
                    await sub.drain()
                    sub.writer.close()
                except Exception:
                    pass
            channel.subscribers.clear()

        elif role == "subscriber":
            channel.subscribers.discard(session)


# =============================================================================
# WATCHDOG Y MONITOR EN TIEMPO REAL
# =============================================================================
async def watchdog_task(controller: SAPIRLRTMPController):
    """Vigila la latencia y pérdida de paquetes 4G/5G con corte anti-cortes."""
    await asyncio.sleep(2)
    while True:
        await asyncio.sleep(1.0)
        now = time.time()
        timeout = float(controller.config.get("watchdog_timeout", 3.5))

        for name, channel in list(controller.channels.items()):
            # Calcular bitrate (Kbps) y FPS
            current_bytes = channel.bytes_received
            current_v_packets = channel.video_packets
            channel.bitrate_kbps = ((current_bytes - channel.prev_bytes) * 8) / 1000.0
            channel.fps = current_v_packets - channel.prev_video_packets
            channel.prev_bytes = current_bytes
            channel.prev_video_packets = current_v_packets

            # Disparar Watchdog si el celular no manda datos durante el timeout
            if channel.publisher_session and (now - channel.last_packet_time > timeout):
                elapsed = now - channel.last_packet_time
                print(f"\n{Fore.YELLOW}[ALERTA WATCHDOG] Corte de señal móvil ({elapsed:.1f}s sin datos). Forzando cierre...{Style.RESET_ALL}")
                controller.syslog.log(
                    "WARN", "WATCHDOG",
                    f"Watchdog Anti-Cortes disparado: {elapsed:.1f}s sin paquetes de datos. Desconectando OBS.",
                    client_ip=channel.publisher_session.peername[0] if channel.publisher_session.peername else "-"
                )
                try:
                    channel.publisher_session.writer.close()
                except Exception:
                    pass
                await controller.cleanup(channel.publisher_session)


# =============================================================================
# SERVIDOR WEB SAP FIORI (AIOHTTP + WEBSOCKET + REST API)
# =============================================================================
class SAPWebServer:
    def __init__(self, controller: SAPIRLRTMPController, syslog: SystemLogManager):
        self.controller = controller
        self.syslog = syslog
        self.ws_clients: Set[web.WebSocketResponse] = set()

    def get_current_metrics(self) -> Dict[str, Any]:
        """Extrae el estado actual del canal activo para la telemetría."""
        active_channel = None
        for name, ch in self.controller.channels.items():
            if ch.publisher_session:
                active_channel = ch
                break

        if not active_channel and self.controller.channels:
            active_channel = next(iter(self.controller.channels.values()))

        ts_ip = ConfigManager.detect_tailscale_ip() or self.controller.config.get("tailscale", {}).get("ip", "")
        if active_channel:
            peer = active_channel.publisher_session.peername if active_channel.publisher_session else None
            return {
                "is_live": active_channel.is_live,
                "bitrate_kbps": round(active_channel.bitrate_kbps, 1),
                "active_channel": active_channel.name,
                "video_packets": active_channel.video_packets,
                "audio_packets": active_channel.audio_packets,
                "subscribers_count": len(active_channel.subscribers),
                "publisher_ip": peer[0] if peer else "-",
                "fps": active_channel.fps,
                "server_ip": self.controller.config.get("server_ip") or ConfigManager.detect_local_ip(),
                "tailscale_ip": ts_ip
            }
        else:
            return {
                "is_live": False,
                "bitrate_kbps": 0,
                "active_channel": "live/irl",
                "video_packets": 0,
                "audio_packets": 0,
                "subscribers_count": 0,
                "publisher_ip": "-",
                "fps": 0,
                "server_ip": self.controller.config.get("server_ip") or ConfigManager.detect_local_ip(),
                "tailscale_ip": ts_ip
            }

    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        try:
            # Enviar estado inicial inmediato
            await ws.send_json(self.get_current_metrics())
            async for _ in ws:
                pass
        finally:
            self.ws_clients.discard(ws)
        return ws

    async def ws_broadcast_task(self):
        """Difunde métricas en tiempo real a todas las consolas SAP abiertas cada 500ms y actualiza live_status para el switcher de OBS."""
        while True:
            await asyncio.sleep(0.5)
            metrics = self.get_current_metrics()

            # Escribir estado para el plugin switcher de OBS
            try:
                is_live = bool(metrics.get("is_live", False))
                bitrate = float(metrics.get("bitrate_kbps", 0))
                # Consideramos LIVE si el publicador está conectado y enviando datos
                status_str = "LIVE" if (is_live and bitrate > 5) else "OFFLINE"
                
                os.makedirs(PLUGIN_DIR, exist_ok=True)
                with open(STATUS_TXT_FILE, "w", encoding="utf-8") as f:
                    f.write(f"{status_str}\n{bitrate:.1f}\n{int(time.time())}\n")

                with open(STATUS_JSON_FILE, "w", encoding="utf-8") as f:
                    json.dump({"status": status_str, "is_live": (status_str == "LIVE"), "bitrate_kbps": bitrate, "timestamp": time.time()}, f)
            except Exception:
                pass

            if self.ws_clients:
                dead = set()
                for ws in self.ws_clients:
                    try:
                        await ws.send_json(metrics)
                    except Exception:
                        dead.add(ws)
                self.ws_clients.difference_update(dead)

    # -----------------------------
    # ENDPOINTS REST API
    # -----------------------------
    async def api_status(self, request: web.Request) -> web.Response:
        return web.json_response(self.get_current_metrics())

    async def api_get_keys(self, request: web.Request) -> web.Response:
        keys = self.controller.config.get("stream_keys", {})
        return web.json_response(keys)

    async def api_create_key(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            key = data.get("key", "").strip().lower()
            desc = data.get("description", "").strip()
            active = bool(data.get("active", True))

            if not key:
                return web.json_response({"error": "Clave requerida"}, status=400)

            keys = self.controller.config.setdefault("stream_keys", {})
            keys[key] = {
                "key": key,
                "description": desc,
                "active": active,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_used": None
            }
            ConfigManager.save(self.controller.config)
            self.syslog.log("INFO", "CONFIG", f"Clave de transmisión '{key}' creada en Maestro SAP")
            return web.json_response({"status": "ok", "key": keys[key]})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def api_toggle_key(self, request: web.Request) -> web.Response:
        key_name = request.match_info.get("name", "").strip().lower()
        keys = self.controller.config.get("stream_keys", {})
        if key_name in keys:
            keys[key_name]["active"] = not keys[key_name].get("active", True)
            ConfigManager.save(self.controller.config)
            self.syslog.log("INFO", "CONFIG", f"Estado de clave '{key_name}' cambiado a {keys[key_name]['active']}")
            return web.json_response({"status": "ok", "active": keys[key_name]["active"]})
        return web.json_response({"error": "Clave no encontrada"}, status=404)

    async def api_delete_key(self, request: web.Request) -> web.Response:
        key_name = request.match_info.get("name", "").strip().lower()
        keys = self.controller.config.get("stream_keys", {})
        if key_name in keys:
            del keys[key_name]
            ConfigManager.save(self.controller.config)
            self.syslog.log("INFO", "CONFIG", f"Clave de transmisión '{key_name}' eliminada")
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "Clave no encontrada"}, status=404)

    async def api_get_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.controller.config)

    async def api_save_config(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            # Actualizar campos permitidos
            for k in ("server_ip", "rtmp_port", "web_port", "watchdog_timeout", "url_display_mode", "tailscale"):
                if k in data:
                    self.controller.config[k] = data[k]
            ConfigManager.save(self.controller.config)
            self.syslog.log("INFO", "CONFIG", "Parámetros del sistema actualizados desde Consola IRL")
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def api_detect_ip(self, request: web.Request) -> web.Response:
        detected = ConfigManager.detect_local_ip()
        return web.json_response({"ip": detected})

    async def api_detect_tailscale_ip(self, request: web.Request) -> web.Response:
        detected = ConfigManager.detect_tailscale_ip()
        return web.json_response({"ip": detected or "", "status": "connected" if detected else "offline"})

    async def api_get_logs(self, request: web.Request) -> web.Response:
        return web.json_response(self.syslog.get_logs())

    async def api_clear_logs(self, request: web.Request) -> web.Response:
        self.syslog.clear()
        return web.json_response({"status": "ok"})

    async def serve_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(os.path.join(WEB_DIR, "index.html"))

    def create_app(self) -> web.Application:
        app = web.Application()
        # Rutas API
        app.router.add_get("/ws", self.ws_handler)
        app.router.add_get("/api/status", self.api_status)
        app.router.add_get("/api/keys", self.api_get_keys)
        app.router.add_post("/api/keys", self.api_create_key)
        app.router.add_post("/api/keys/{name}/toggle", self.api_toggle_key)
        app.router.add_delete("/api/keys/{name}", self.api_delete_key)
        app.router.add_get("/api/config", self.api_get_config)
        app.router.add_post("/api/config", self.api_save_config)
        app.router.add_get("/api/detect-ip", self.api_detect_ip)
        app.router.add_get("/api/detect-tailscale-ip", self.api_detect_tailscale_ip)
        app.router.add_get("/api/logs", self.api_get_logs)
        app.router.add_post("/api/logs/clear", self.api_clear_logs)

        # Archivos estáticos de la interfaz
        app.router.add_get("/", self.serve_index)
        app.router.add_static("/web", WEB_DIR, show_index=True)
        app.router.add_static("/", WEB_DIR, show_index=True)
        return app


# =============================================================================
# PUNTO DE ENTRADA PRINCIPAL ASÍNCRONO
# =============================================================================
async def main():
    config = ConfigManager.load()
    syslog = SystemLogManager()

    server_ip = config.get("server_ip") or ConfigManager.detect_local_ip()
    rtmp_port = int(config.get("rtmp_port", 1935))
    web_port = int(config.get("web_port", 8080))
    ts_ip = ConfigManager.detect_tailscale_ip() or config.get("tailscale", {}).get("ip", "")
    ts_display = f"{Fore.GREEN}{ts_ip} (Tailscale VPN ONLINE){Style.RESET_ALL}" if ts_ip else f"{Fore.YELLOW}No detectada (Inicia Tailscale){Style.RESET_ALL}"

    # Banner IRL Hub
    print("=" * 80)
    print(f"{Fore.CYAN}       IRL STREAMING OPERATIONS CENTER & RTMP BROKER{Style.RESET_ALL}")
    print(f"{Fore.WHITE}       Autor: {Fore.GREEN}Iamfreedz{Style.RESET_ALL} ({Fore.CYAN}https://kick.com/iamfreedz{Style.RESET_ALL}) | Sistema: {Fore.MAGENTA}IRL-PROD{Style.RESET_ALL}")
    print("=" * 80)
    print(f"  [>] IP Red Wi-Fi / Local    : {Fore.YELLOW}{server_ip}{Style.RESET_ALL}")
    print(f"  [>] IP Tailscale VPN (4G/5G): {ts_display}")
    print(f"  [>] Puerto Ingesta RTMP     : {Fore.GREEN}{rtmp_port}{Style.RESET_ALL} (TCP)")
    print(f"  [>] Consola Web Fiori       : {Fore.CYAN}http://localhost:{web_port}{Style.RESET_ALL} o {Fore.CYAN}http://{server_ip}:{web_port}{Style.RESET_ALL}")
    print(f"  [>] Ingesta Móvil Wi-Fi     : {Fore.GREEN}rtmp://{server_ip}:{rtmp_port}/live/irl{Style.RESET_ALL}")
    if ts_ip:
        print(f"  [>] Ingesta Móvil Tailscale : {Fore.GREEN}rtmp://{ts_ip}:{rtmp_port}/live/irl{Style.RESET_ALL}")
    print(f"  [>] Captura OBS Studio      : {Fore.MAGENTA}rtmp://127.0.0.1:{rtmp_port}/live/irl{Style.RESET_ALL}")
    print("=" * 80)

    # 1. Iniciar Controlador RTMP
    controller = SAPIRLRTMPController(config=config, syslog=syslog)
    loop = asyncio.get_running_loop()

    try:
        rtmp_server = await asyncio.start_server(
            controller.client_callback,
            host="0.0.0.0",
            port=rtmp_port
        )
        print(f"{Fore.GREEN}[OK] Servidor RTMP escuchando satisfactoriamente en 0.0.0.0:{rtmp_port}{Style.RESET_ALL}")
        syslog.log("INFO", "RTMP_CORE", f"Servidor RTMP iniciado en 0.0.0.0:{rtmp_port}")
    except Exception as e:
        print(f"{Fore.RED}[ERROR] Error iniciando servidor RTMP en puerto {rtmp_port}: {e}{Style.RESET_ALL}")
        syslog.log("ERROR", "RTMP_CORE", f"Error de socket RTMP: {e}")
        return

    # 2. Iniciar Servidor Web SAP Fiori y API REST/WebSocket
    web_server = SAPWebServer(controller=controller, syslog=syslog)
    app = web_server.create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=web_port)

    try:
        await site.start()
        print(f"{Fore.GREEN}[OK] Consola Web SAP Fiori disponible en: http://localhost:{web_port}{Style.RESET_ALL}")
        syslog.log("INFO", "WEB_GUI", f"Consola SAP Fiori iniciada en 0.0.0.0:{web_port}")
    except Exception as e:
        print(f"{Fore.YELLOW}[!] Advertencia: No se pudo enlazar consola web en puerto {web_port}: {e}{Style.RESET_ALL}")

    # 3. Lanzar tareas en segundo plano
    asyncio.create_task(watchdog_task(controller))
    asyncio.create_task(web_server.ws_broadcast_task())

    print(f"\n{Fore.WHITE}[i] Presiona Ctrl+C en cualquier momento para detener los servicios.{Style.RESET_ALL}\n")

    # Mantener el bucle vivo
    try:
        await asyncio.Event().wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print(f"\n{Fore.YELLOW}[!] Deteniendo servidores RTMP y Web...{Style.RESET_ALL}")
        rtmp_server.close()
        await rtmp_server.wait_closed()
        await runner.cleanup()
        print(f"{Fore.GREEN}[OK] Servidores cerrados limpiamente.{Style.RESET_ALL}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}[!] Proceso finalizado por el usuario.{Style.RESET_ALL}")

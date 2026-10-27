# 🎬 IRL Auto Scene Switcher V2 para OBS Studio

Conmutador automático inteligente para directos **IRL (In Real Life)** en Kick/Twitch.

---

## ⚡ ¿Qué se corrigió en la Versión 2 (V2)?

1. **Fin a los saltos intermitentes (Bucle de escenas):**
   * Se incorporó un **Cooldown Anti-Rebote de 4 segundos**. Una vez que OBS cambia de escena, no vuelve a cambiar de inmediato, permitiendo que la transición termine y el video decodifique sin parpadear.
2. **Detección Directa del Servidor RTMP (`live_status.txt`):**
   * En lugar de depender únicamente de los estados internos de OBS (que a veces marcan `0` para enlaces de red RTMP), el script ahora lee directamente el estado del servidor local de tu PC. Si el celular en la calle está enviando datos por 4G/5G, el servidor marca `LIVE` al instante.
3. **Respaldo por Dimensiones de Video:**
   * Si por alguna razón el servidor estuviera apagado, el script analiza los píxeles reales del video (`width > 0`) en lugar de estados confusos de reproducción.

---

## ⚠️ AJUSTES CRÍTICOS EN LA FUENTE MULTIMEDIA DE OBS (IMPORTANTE)

En la ventana de **Propiedades de tu Fuente Multimedia** en OBS Studio:
* ❌ **"Reiniciar la reproducción cuando la fuente esté activa":** **DEBE ESTAR DESMARCADO**.
  *(En emisiones en vivo RTMP, tener esta casilla marcada provoca que OBS intente reiniciar el flujo cada vez que cambia de escena, generando cortes de búfer, congelamientos y saltos entre escenas).*
* ❌ **"Cerrar el archivo cuando esté inactivo":** **DEBE ESTAR DESMARCADO**.
  *(Si marcas esta casilla, en cuanto OBS cambie a la escena de Pausa cerrará el socket local y no podrá recibir el video de tu celular cuando vuelva la señal).*
* ❌ **"Archivo local":** **DESMARCADO**.
  *(Obligatorio para que OBS muestre los campos de red RTMP en lugar de buscar un archivo en tu disco duro).*

---

## 🚀 Cómo recargar el script en OBS Studio:

1. En **OBS Studio**, ve al menú superior: **Herramientas (Tools) ➔ Scripts**.
2. Selecciona `irl_auto_scene_switcher.lua` en la lista.
3. Haz clic en el botón **🔄 (Recargar script)** (o quítalo con `-` y vuelve a agregarlo con `+`).
4. Configura los campos:
   * **🟢 Escena EN VIVO:** Tu escena principal (ej: `IRL - En Vivo`).
   * **🔴 Escena PAUSA / BRB:** Tu escena de espera (ej: `Volvemos Enseguida`).
   * **📡 Método de Detección:** `⚡ Automático / Híbrido [Recomendado]`.
   * **📹 Fuente Multimedia:** Tu fuente de cámara RTMP.
   * **⏱️ Espera antes de Pausa:** `3.0` segundos.
   * **⚡ Espera antes de En Vivo:** `1.0` segundo.

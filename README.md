# Ammadis Vision

Caja de borde para operaciones de piso en restaurantes de Unique Concepts.
Mira cámaras RTSP, aplica reglas de zona (entrada, comedor, barra, cocina) y
publica eventos JSON a un webhook. El primer sitio de ejemplo es **Gaucho
Grill Temecula**, con un NVR TVT por RTSP.

Toast queda fuera de este MVP. El stub de la nube solo recibe el webhook,
lo escribe en el log y lo guarda en SQLite para poder enseñar el flujo sin
un POS.

**Privacidad.** No hay reconocimiento facial, ni galería de rostros, ni
nombres de personas. La detección es de persona u objeto. `sub_label`,
atributos de cara, miniaturas y placas se tiran en el normalizador y no
existen en el evento que sale de la caja. Identificar al personal por
uniforme puede venir después; no se hace con biometría.

## Arquitectura

```mermaid
flowchart LR
  nvr["NVR TVT · substream RTSP"] --> frigate["Frigate 0.16 · motor de visión"]
  frigate -->|"MQTT frigate/events"| edge["Plano de control Ammadis"]
  edge -->|"JSON zone events"| stub["Cloud stub"]
  stub --> db["SQLite"]
```

Frigate es el motor por defecto y **es una dependencia reemplazable**. El
plano de control (`services/edge`) no importa tipos de Frigate en las reglas.
Solo entiende un `TrackUpdate`: id de track, cámara, clase (`person`),
score, zonas actuales y si el track terminó. MQTT es el camino en vivo.
Si el broker se cae, el servicio consulta `GET /api/events` de Frigate como
respaldo degradado (esas zonas son acumuladas, no la posición en vivo).

| Servicio | Imagen / build | Puerto del host | Función |
| --- | --- | --- | --- |
| `mosquitto` | eclipse-mosquitto 2.0 | 1883 | Broker local, sin usuario en este MVP |
| `frigate` | ghcr.io/blakeblackshear/frigate:0.16.2 | 8971 | Ingesta RTSP y detección. UI con login |
| `edge` | `services/edge` | 8088 | Reglas, debounce, salud, webhook |
| `cloud-stub` | `services/cloud-stub` | 8090 | Recibe y guarda eventos |

El puerto 5000 de Frigate (API interna sin login) no se publica. El edge lo
usa dentro de la red de Docker.

## Runbook en Ubuntu

Probado con Docker Engine y el plugin Compose v2 (`docker compose`, no
`docker-compose` suelto). Hace falta un mini-PC Linux en la LAN del NVR.
La detección de este ejemplo es por CPU, a 640×360 y 5 fps en el substream,
para poder arrancar sin Coral. En un sitio real conviene un detector Coral
u OpenVINO; el comentario está en `docker-compose.yml`.

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# cerrar sesión y volver a entrar para que el grupo docker aplique
```

```bash
git clone https://github.com/adrianamosa/Ammadis-Vision.git
cd Ammadis-Vision
cp .env.example .env
```

Editar `.env`:

- `FRIGATE_NVR_HOST`, `FRIGATE_RTSP_USER`, `FRIGATE_RTSP_PASSWORD` — el NVR.
  Si la clave tiene `@`, `:`, `/` o `#`, hay que escribirla en percent-encoding.
- `AMMADIS_WEBHOOK_URL` — a dónde van los eventos. El valor de ejemplo apunta
  al stub (`http://cloud-stub:8080/webhooks/ammadis`).
- `AMMADIS_ALLOW_INJECT` — `true` solo para simular tracks con
  `POST /debug/inject`. En el local poner `false`.

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8088/health
curl -fsS http://127.0.0.1:8088/ready
curl -fsS http://127.0.0.1:8090/health
```

`/health` responde 200 en cuanto el proceso está vivo e incluye
`mqtt_connected` y la ocupación. `/ready` responde 503 hasta que el edge
está suscrito a MQTT.

La primera vez, Frigate crea el usuario `admin` y escribe la contraseña en
el log. Entrar a `http://<ip-de-la-caja>:8971` y cambiarla:

```bash
docker compose logs frigate | grep -i password
```

Hasta que el RTSP no responda, FFmpeg va a reintentar y el log de Frigate se
llena de errores de conexión. El edge y el stub igual arrancan. No hace falta
una cámara para ver el contrato de eventos: `scripts/demo_pipeline.sh`.

## Apuntar el NVR TVT

El ejemplo asume el substream, que es el que debe alimentar `detect`. El
mainstream no se usa.

Forma habitual en TVT (canal en base 1):

```text
rtsp://USUARIO:CLAVE@IP_NVR:554/chID=1&streamType=sub&linkType=tcp
```

En `config/frigate/config.yml` los cuatro canales placeholder son:

| Canal | Cámara | Zona Frigate | Zona de piso |
| --- | --- | --- | --- |
| 1 | `cam_entrance` | `entrance` | Entrada |
| 2 | `cam_dining` | `dining` | Comedor |
| 3 | `cam_bar` | `bar` | Barra |
| 4 | `cam_kitchen` | `kitchen` | Cocina |

Host, usuario y clave salen de `.env` (`{FRIGATE_NVR_HOST}`,
`{FRIGATE_RTSP_USER}`, `{FRIGATE_RTSP_PASSWORD}`). No van en el YAML.

Algunos firmware TVT publican el substream como `/live/ch00_1` (canal en
base 0, índice 1 = sub). El path correcto está en la web del NVR. Si cambia,
se edita solo el `path` de esa cámara y se reinicia Frigate:

```bash
docker compose up -d frigate
```

Los polígonos del config cubren casi todo el cuadro para que una detección
cuente dentro de la zona antes de calibrar. Con imagen real, dibujar la zona
en la UI de Frigate (Settings → la cámara → Zones). El **nombre** de la zona
(`dining`, no el friendly name "Comedor") tiene que seguir igual que
`engine_zones` en el YAML del sitio. El nombre de la zona no puede ser igual
al de la cámara; por eso las cámaras se llaman `cam_dining` y la zona `dining`.

Grabación, snapshots y el JPEG por MQTT están apagados. La UI de Frigate
puede seguir guardando una miniatura local del track; el plano de control no
la reenvía. No publicar el puerto 8971 a internet.

`face_recognition`, `lpr` y `semantic_search` están en `enabled: false`.
No activarlos: el servicio además rechaza las clases `face` e `identity`
si alguien las agrega a `labels`.

## Zonas y debounce

`config/sites/gaucho-grill-temecula/site.yaml` es la config del plano de
control, distinta de los polígonos de Frigate.

- `enter_seconds: 2` — la persona tiene que seguir en la zona ese tiempo
  antes de `person_entered_zone`.
- `leave_seconds: 5` — tiene que seguir fuera ese tiempo antes de
  `person_left_zone`, para no contar una oclusión de un segundo.
- `track_timeout_seconds: 0` — un comensal sentado no se da de baja porque
  el motor deje de publicar. El track se cierra cuando Frigate manda el fin.
  Subir este valor solo si se quiere un tope de seguridad; un valor corto
  genera salidas falsas.

Cada transición confirmada emite el evento de la persona y, además,
`zone_occupancy_changed`. Si alguien cruza de comedor a barra, los debounce
son independientes: puede figurar en las dos zonas unos segundos. Es el
comportamiento elegido para no parpadear.

Variables que pisan al YAML sin editarlo: `AMMADIS_WEBHOOK_URL`,
`AMMADIS_MQTT_HOST`, `AMMADIS_MQTT_PORT`, `AMMADIS_MQTT_TOPIC`,
`AMMADIS_FRIGATE_API_URL`, `AMMADIS_HTTP_FALLBACK_SECONDS`.

## Webhook

`POST` de un JSON por evento, `Content-Type: application/json`, cabeceras
`X-Ammadis-Event` y `X-Ammadis-Event-Id`. Si la URL está vacía, el edge
escribe el evento en su log y no llama a nadie. Los fallos se reintentan
(`webhook.retries`) y no tiran el proceso. El contador queda en `/health`.

El stub:

```bash
curl -fsS http://127.0.0.1:8090/events
curl -fsS "http://127.0.0.1:8090/events?zone_id=dining&event_type=person_entered_zone"
curl -fsS http://127.0.0.1:8090/events/EVENT_ID
curl -fsS -X DELETE http://127.0.0.1:8090/events
```

Un `event_id` repetido responde 200 con `"duplicate": true` y no se inserta
otra vez. Los ejemplos completos están en [docs/events.md](docs/events.md).

```json
{
  "schema": "ammadis.vision.event.v1",
  "event_type": "person_entered_zone",
  "site": {"id": "gaucho-grill-temecula", "name": "Gaucho Grill Temecula"},
  "zone": {"id": "dining", "name": "Comedor"},
  "camera": "cam_dining",
  "label": "person",
  "track_id": "demo-track-dining-1",
  "occupancy": 1,
  "previous_occupancy": 0,
  "reason": "enter",
  "source": {"engine": "frigate", "transport": "mqtt"}
}
```

## Salud

`GET http://127.0.0.1:8088/health`

```json
{
  "status": "ok",
  "service": "ammadis-edge",
  "site_id": "gaucho-grill-temecula",
  "vision_engine": "frigate",
  "mqtt_connected": true,
  "frigate_api": "ok",
  "webhook_configured": true,
  "occupancy": {"entrance": 0, "dining": 0, "bar": 0, "kitchen": 0}
}
```

También: `GET /ready`, `GET /occupancy`, `GET /events/recent` (los últimos
eventos emitidos, en memoria, por si el webhook está caído).

`frigate_api` pasa a `ok` cuando `http://frigate:5000/api/version` responde.
Con las cámaras placeholder puede seguir en `down` hasta que el contenedor
de Frigate terminó de arrancar, o si el contenedor está reiniciando.

## Probar sin cámaras

Con el stack arriba:

```bash
chmod +x scripts/demo_pipeline.sh
./scripts/demo_pipeline.sh
```

El script publica en Mosquitto un evento real de Frigate que incluye
`sub_label` y un atributo `face` a propósito. En el stub solo debe aparecer
`person` en Comedor, luego la salida. Esperar ~2 s para la entrada y ~5 s
para la salida.

Otra vía, con `AMMADIS_ALLOW_INJECT=true`:

```bash
curl -fsS -X POST http://127.0.0.1:8088/debug/inject \
  -H 'Content-Type: application/json' \
  -d @fixtures/frigate_enter_dining.json
```

Ese endpoint no existe (404) cuando el flag está en false. Un track con
`label: face` responde 400.

## Cambiar el motor de visión

Frigate se puede quitar sin reescribir las reglas.

1. Implementar `start(on_track)`, `stop()` y `health()` en
   `services/edge/ammadis_edge/sources.py`. `on_track` recibe un
   `TrackUpdate` (definido en `models.py`). Prohibido pasar nombres,
   embeddings, recortes de cara o `sub_label`.
2. Registrar el nombre en `build_source`. Hoy el único registrado es
   `frigate`.
3. En el YAML del sitio, `vision.engine` pasa a ese nombre. `engine_zones`
   son los nombres de zona **de ese motor**. `cameras` son las cámaras que
   pueden ver la zona de piso.
4. `rules.py` no se toca para el cambio de motor. Un test comprueba que ese
   módulo no menciona a Frigate ni a MQTT.
5. En Compose, el servicio `frigate` se puede parar o sustituir. El edge
   solo necesita la fuente nueva y, si ya no hay MQTT, un `health()` con
   `ready: true` para que `/ready` no exija al broker.

El contrato que no cambia es el JSON de [docs/events.md](docs/events.md).

## Límites de este MVP

- Un sitio, un YAML, sin multi-tenant.
- Sin API de Toast y sin cruce con tickets.
- Sin credenciales reales de Temecula. Las del `.env.example` son falsas.
- Mosquitto acepta anónimos y el inject puede estar abierto. La caja va en
  la LAN del restaurante, no expuesta a internet. Endurecer queda fuera.
- Si el proceso del edge se reinicia, la ocupación en memoria vuelve a cero
  hasta el próximo track. No es un conteo histórico.
- El detector CPU con cuatro cámaras se queda corto para producción.

Tests:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## English

On-prem edge box for Unique Concepts floor ops. Frigate (pinned 0.16.2) reads
the NVR substreams and detects persons. Our Python service is the control
plane: it subscribes to `frigate/events`, debounces zone enter/leave, and
POSTs `person_entered_zone`, `person_left_zone`, and `zone_occupancy_changed`
to `AMMADIS_WEBHOOK_URL`. A small Docker stub stores those webhooks in SQLite
so you can demo without Toast.

No face recognition and no face gallery. Frigate's face recognition, LPR, and
semantic search are off, and the normalizer drops `sub_label`, face
attributes, and snapshots. Do not turn those on.

```bash
cp .env.example .env
# set the TVT NVR host, RTSP user/password, and webhook URL
docker compose up -d --build
curl -fsS http://127.0.0.1:8088/health
./scripts/demo_pipeline.sh
```

Temecula placeholders live in `config/frigate/config.yml` (RTSP) and
`config/sites/gaucho-grill-temecula/site.yaml` (zones: entrance, dining, bar,
kitchen). Typical TVT substream:
`rtsp://USER:PASS@NVR:554/chID=1&streamType=sub&linkType=tcp`.
Redraw zone polygons in the Frigate UI; keep the zone ids. The Spanish
sections above are the full runbook, including how to swap Frigate: implement
`VisionSource`, register it in `build_source`, and point `vision.engine` at
it. Event shapes: [docs/events.md](docs/events.md).

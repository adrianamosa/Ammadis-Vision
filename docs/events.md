# Eventos del plano de control

Esquema `ammadis.vision.event.v1`. Un POST por evento a `webhook.url`
(`/webhooks/ammadis` en el stub). No hay imágenes, nombres, `sub_label`,
placas ni embeddings. `track_id` es el id anónimo del motor de visión.

`occurred_at` es el momento en que la regla confirmó el cambio (después del
debounce), en UTC. `detected_at` es el timestamp del cuadro, si el motor lo
mandó. `occupancy` es el conteo de la zona después del cambio.

La entrada y la salida de una persona emiten también `zone_occupancy_changed`,
para quien solo quiera el conteo (el cruce con Toast, más adelante, puede
usar ese evento).

## person_entered_zone

```json
{
  "schema": "ammadis.vision.event.v1",
  "event_id": "4f1c0a2e-7b3d-4a1e-9c2f-0b6d8e1a2c30",
  "event_type": "person_entered_zone",
  "occurred_at": "2026-11-05T15:04:12.000Z",
  "detected_at": "2026-11-05T15:04:10.120Z",
  "site": {
    "id": "gaucho-grill-temecula",
    "name": "Gaucho Grill Temecula",
    "timezone": "America/Los_Angeles"
  },
  "zone": {
    "id": "dining",
    "name": "Comedor"
  },
  "camera": "cam_dining",
  "label": "person",
  "track_id": "1730811850.12-ab12cd",
  "score": 0.91,
  "occupancy": 3,
  "previous_occupancy": 2,
  "track_ids": [
    "1730811800.10-aa11",
    "1730811850.12-ab12cd",
    "1730811902.40-cc33"
  ],
  "reason": "enter",
  "source": {
    "engine": "frigate",
    "transport": "mqtt"
  }
}
```

## person_left_zone

`reason` es `leave` cuando la persona sale de la zona o el track termina, y
`timeout` solo si `track_timeout_seconds` es mayor que 0.

```json
{
  "schema": "ammadis.vision.event.v1",
  "event_id": "91aa0c44-1d20-4b55-8e10-6c0a9f2b7711",
  "event_type": "person_left_zone",
  "occurred_at": "2026-11-05T15:11:02.000Z",
  "detected_at": "2026-11-05T15:10:57.400Z",
  "site": {
    "id": "gaucho-grill-temecula",
    "name": "Gaucho Grill Temecula",
    "timezone": "America/Los_Angeles"
  },
  "zone": {
    "id": "dining",
    "name": "Comedor"
  },
  "camera": "cam_dining",
  "label": "person",
  "track_id": "1730811850.12-ab12cd",
  "score": 0.88,
  "occupancy": 2,
  "previous_occupancy": 3,
  "track_ids": [
    "1730811800.10-aa11",
    "1730811902.40-cc33"
  ],
  "reason": "leave",
  "source": {
    "engine": "frigate",
    "transport": "mqtt"
  }
}
```

## zone_occupancy_changed

Mismo cuerpo. `track_id` y `score` van en null porque el evento es de la zona,
no de una persona. `reason` dice qué transición lo causó (`enter`, `leave` o
`timeout`). `camera` es la cámara del track que movió el conteo.

```json
{
  "schema": "ammadis.vision.event.v1",
  "event_id": "c3e91b70-88a4-4f0d-9a61-2d5b6e708914",
  "event_type": "zone_occupancy_changed",
  "occurred_at": "2026-11-05T15:04:12.000Z",
  "detected_at": "2026-11-05T15:04:10.120Z",
  "site": {
    "id": "gaucho-grill-temecula",
    "name": "Gaucho Grill Temecula",
    "timezone": "America/Los_Angeles"
  },
  "zone": {
    "id": "dining",
    "name": "Comedor"
  },
  "camera": "cam_dining",
  "label": "person",
  "track_id": null,
  "score": null,
  "occupancy": 3,
  "previous_occupancy": 2,
  "track_ids": [
    "1730811800.10-aa11",
    "1730811850.12-ab12cd",
    "1730811902.40-cc33"
  ],
  "reason": "enter",
  "source": {
    "engine": "frigate",
    "transport": "mqtt"
  }
}
```

## English

Each confirmed zone transition is one JSON object, `Content-Type: application/json`,
header `X-Ammadis-Event` set to the `event_type`. A person entering or leaving
also produces `zone_occupancy_changed`. The cloud stub stores the body as-is and
returns `{"stored": true}` or `{"stored": false, "duplicate": true}` for the same
`event_id`. There is no image and no identity field in this contract.

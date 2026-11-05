"""Site YAML: floor zones, debounce, and where events are posted."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ammadis_edge.models import FORBIDDEN_LABELS


class DebounceConfig(BaseModel):
    enter_seconds: float = Field(default=2.0, ge=0)
    leave_seconds: float = Field(default=5.0, ge=0)
    # 0 keeps a seated guest until the vision engine ends the track.
    # A short timeout false-leaves people who stay still in the dining room.
    track_timeout_seconds: float = Field(default=0.0, ge=0)


class ZoneConfig(BaseModel):
    id: str
    name: str
    cameras: list[str]
    engine_zones: list[str]

    @field_validator("id")
    @classmethod
    def zone_id(cls, value: str) -> str:
        ok = value.replace("_", "")
        if not value or value != value.lower() or not ok.isalnum():
            raise ValueError(
                f"zone id {value!r} must be lowercase letters, numbers, or underscore"
            )
        return value

    @field_validator("cameras", "engine_zones")
    @classmethod
    def non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and str(item).strip()]
        if not cleaned:
            raise ValueError("list at least one name")
        return cleaned


class SiteConfig(BaseModel):
    site_id: str
    site_name: str
    timezone: str = "UTC"
    vision_engine: str = "frigate"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_topic: str = "frigate/events"
    frigate_api_url: str = "http://frigate:5000"
    http_fallback_seconds: float = Field(default=5.0, ge=0)
    http_probe_seconds: float = Field(default=15.0, ge=0)
    webhook_url: str = ""
    webhook_timeout_seconds: float = Field(default=5.0, gt=0)
    webhook_retries: int = Field(default=3, ge=0)
    labels: list[str] = Field(default_factory=lambda: ["person"])
    debounce: DebounceConfig = Field(default_factory=DebounceConfig)
    zones: list[ZoneConfig]

    @field_validator("site_id")
    @classmethod
    def site_slug(cls, value: str) -> str:
        ok = value.replace("-", "")
        if not value or value != value.lower() or not ok.isalnum():
            raise ValueError("site.id must be a lowercase slug")
        return value

    @field_validator("vision_engine")
    @classmethod
    def engine_name(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("vision.engine is required")
        return cleaned

    @field_validator("labels")
    @classmethod
    def labels_are_objects_only(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if item and str(item).strip()]
        if not cleaned:
            raise ValueError("labels must include at least one object class, such as person")
        banned = sorted(set(cleaned) & FORBIDDEN_LABELS)
        if banned:
            raise ValueError(
                "labels cannot include face or identity classes "
                f"({', '.join(banned)}). Detection is person/object only."
            )
        return cleaned

    @model_validator(mode="after")
    def zones_unique(self) -> SiteConfig:
        ids = [zone.id for zone in self.zones]
        if not ids:
            raise ValueError("at least one zone is required")
        if len(ids) != len(set(ids)):
            raise ValueError("zone ids must be unique")
        return self


def _env_str(env: Mapping[str, str], key: str, current: str) -> str:
    raw = env.get(key)
    if raw is None or raw == "":
        return current
    return raw


def _env_int(env: Mapping[str, str], key: str, current: int) -> int:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return current
    return int(raw)


def _env_float(env: Mapping[str, str], key: str, current: float) -> float:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return current
    return float(raw)


def load_site_config(path: str | Path, env: Mapping[str, str] | None = None) -> SiteConfig:
    """Load site YAML. Process env overrides webhook, MQTT, and Frigate URL."""

    environ: Mapping[str, str] = os.environ if env is None else env
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Site config not found at {config_path}. "
            "Set AMMADIS_SITE_CONFIG to the site YAML."
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must be a YAML mapping")

    site = raw.get("site") or {}
    vision = raw.get("vision") or {}
    mqtt = vision.get("mqtt") or {}
    http = vision.get("http") or {}
    webhook = dict(raw.get("webhook") or {})
    debounce = raw.get("debounce") or {}
    if not isinstance(site, dict) or not isinstance(vision, dict):
        raise ValueError(f"{config_path} is missing site or vision")

    webhook["url"] = _env_str(environ, "AMMADIS_WEBHOOK_URL", str(webhook.get("url") or ""))
    fallback = http.get("fallback_poll_seconds", 5)
    probe = http.get("probe_seconds", 15)
    timeout = webhook.get("timeout_seconds", 5)
    retries = webhook.get("retries", 3)
    try:
        config = SiteConfig(
            site_id=str(site.get("id") or ""),
            site_name=str(site.get("name") or ""),
            timezone=str(site.get("timezone") or "UTC"),
            vision_engine=str(vision.get("engine") or "frigate"),
            mqtt_host=_env_str(environ, "AMMADIS_MQTT_HOST", str(mqtt.get("host") or "mosquitto")),
            mqtt_port=_env_int(environ, "AMMADIS_MQTT_PORT", int(mqtt.get("port") or 1883)),
            mqtt_topic=_env_str(
                environ, "AMMADIS_MQTT_TOPIC", str(mqtt.get("topic") or "frigate/events")
            ),
            frigate_api_url=_env_str(
                environ,
                "AMMADIS_FRIGATE_API_URL",
                str(http.get("base_url") or "http://frigate:5000"),
            ).rstrip("/"),
            http_fallback_seconds=_env_float(
                environ, "AMMADIS_HTTP_FALLBACK_SECONDS", float(fallback)
            ),
            http_probe_seconds=_env_float(environ, "AMMADIS_HTTP_PROBE_SECONDS", float(probe)),
            webhook_url=str(webhook.get("url") or ""),
            webhook_timeout_seconds=float(timeout),
            webhook_retries=int(retries),
            labels=list(raw.get("labels") or ["person"]),
            debounce=DebounceConfig.model_validate(debounce),
            zones=[ZoneConfig.model_validate(zone) for zone in (raw.get("zones") or [])],
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid site config {config_path}: {exc}") from exc
    return config


def allow_inject_from_env(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return environ.get("AMMADIS_ALLOW_INJECT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

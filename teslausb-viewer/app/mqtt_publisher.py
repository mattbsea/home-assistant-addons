"""Publish statistics to Home Assistant via MQTT discovery.

All sensors are grouped under one device, "TeslaUSB Viewer". Discovery configs are
retained and published once on connect; states are republished after every index scan.
Byte-count sensors that the backend can't report (e.g. if shutil.disk_usage fails when
teslacam_path is not mounted) are published as the MQTT "unavailable" payload rather than omitted.
"""

from __future__ import annotations

import json
import logging

from .config import Settings

log = logging.getLogger("teslausb_viewer.mqtt")

DEVICE = {
    "identifiers": ["teslausb_viewer"],
    "name": "TeslaUSB Viewer",
    "manufacturer": "TeslaUSB Viewer Add-on",
    "model": "TeslaUSB Viewer",
}

AVAILABILITY_TOPIC = "teslausb_viewer/status"
DISCOVERY_PREFIX = "homeassistant"

# key -> (friendly name, device_class, unit, icon)
SENSORS = {
    "total_events":        ("Total Events",        None,        None,    "mdi:filmstrip-box-multiple"),
    "savedclips_count":    ("Saved Clips",         None,        None,    "mdi:content-save"),
    "sentryclips_count":   ("Sentry Clips",        None,        None,    "mdi:shield-car"),
    "recentclips_count":   ("Recent Clips",        None,        None,    "mdi:history"),
    "total_video_files":   ("Total Video Files",   None,        None,    "mdi:video"),
    "today_sentry_count":  ("Sentry Events Today", None,        None,    "mdi:shield-alert"),
    "last_event":          ("Last Event",          "timestamp", None,    "mdi:clock-outline"),
    "last_index_refresh":  ("Last Index Refresh",  "timestamp", None,    "mdi:database-refresh"),
    "backend_used_bytes":  ("Backend Used",        "data_size", "B",     "mdi:cloud-upload"),
    "backend_free_bytes":  ("Backend Free",        "data_size", "B",     "mdi:cloud"),
}

UNAVAILABLE = "unavailable"


class MqttPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        # Defensive throughout: a broker problem must never take down the web UI.
        if not self.settings.mqtt_enabled or not self.settings.mqtt_host:
            log.info("MQTT publishing disabled")
            return
        try:
            import paho.mqtt.client as mqtt

            # paho 2.x changed the constructor; pin the v1 callback API to match our
            # on_connect(client, userdata, flags, rc) signature explicitly.
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="teslausb_viewer")
            if self.settings.mqtt_username:
                client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
            client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
            client.on_connect = self._on_connect
            client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
            client.loop_start()
            self._client = client
        except Exception as exc:  # noqa: BLE001 — never propagate MQTT failures
            log.warning("MQTT unavailable, continuing without statistics entities: %s", exc)
            self._client = None

    def _on_connect(self, client, _userdata, _flags, rc) -> None:
        self._connected = rc == 0
        if rc != 0:
            log.warning("MQTT connect failed (rc=%s)", rc)
            return
        log.info("MQTT connected — publishing discovery")
        client.publish(AVAILABILITY_TOPIC, "online", retain=True)
        for key, (name, dev_class, unit, icon) in SENSORS.items():
            cfg = {
                "name": name,
                "unique_id": f"teslausb_viewer_{key}",
                "state_topic": f"teslausb_viewer/{key}",
                "availability_topic": AVAILABILITY_TOPIC,
                "device": DEVICE,
                "icon": icon,
            }
            if dev_class:
                cfg["device_class"] = dev_class
            if unit:
                cfg["unit_of_measurement"] = unit
            client.publish(
                f"{DISCOVERY_PREFIX}/sensor/teslausb_viewer/{key}/config",
                json.dumps(cfg), retain=True,
            )

    def publish_states(self, stats: dict) -> None:
        if not self._client or not self._connected:
            return
        for key in SENSORS:
            value = stats.get(key)
            payload = UNAVAILABLE if value is None else str(value)
            self._client.publish(f"teslausb_viewer/{key}", payload, retain=True)

    def stop(self) -> None:
        if self._client:
            self._client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()

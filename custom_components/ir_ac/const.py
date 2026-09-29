"""Constants for the IR Air Conditioner integration."""

from typing import Final

DOMAIN: Final = "ir_ac"

# User-supplied code maps live here (one JSON file per map, same format as maps/).
USER_MAPS_DIR: Final = "ir_ac_maps"

CONF_CODE_MAP: Final = "code_map"
CONF_EMITTER: Final = "emitter"
CONF_RECEIVER: Final = "receiver"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_HUMIDITY_SENSOR: Final = "humidity_sensor"
CONF_FEATURES: Final = "features"

# Fired when a frame from the physical remote is decoded.
EVENT_REMOTE_COMMAND: Final = "ir_ac_remote_command"

# Changes made in quick succession (e.g. Apple Home sending mode + temperature)
# are merged into one transmission.
SEND_DELAY: Final = 0.3
# A received frame matching what we sent within this window is our own echo.
ECHO_WINDOW: Final = 3.0

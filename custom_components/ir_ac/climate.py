"""Climate entity for an IR-controlled air conditioner.

Commands go out through an infrared emitter entity (e.g. an ESPHome IR proxy).
If a receiver entity is configured, frames from the unit's own remote are
decoded and the entity state follows them. Air conditioners do not report
their state over IR, so the entity is otherwise optimistic (assumed state).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from infrared_protocols.commands import Command

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.infrared import (
    InfraredEmitterConsumerEntity,
    InfraredReceivedSignal,
    async_subscribe_receiver,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData, RestoreEntity

from .codec import (
    FIELD_FAN,
    FIELD_MODE,
    FIELD_POWER,
    FIELD_SWING,
    FIELD_SWING_HORIZONTAL,
    FIELD_TEMPERATURE,
    CodeMap,
)
from .const import (
    CONF_CODE_MAP,
    CONF_EMITTER,
    CONF_FEATURES,
    CONF_HUMIDITY_SENSOR,
    CONF_MIRROR_TARGET,
    CONF_RECEIVER,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
    ECHO_WINDOW,
    EVENT_REMOTE_COMMAND,
    SEND_DELAY,
)
from .maps import async_get_code_maps

_LOGGER = logging.getLogger(__name__)

# Code-map mode names are Home Assistant HVAC mode values.
_VALID_MODES = {m.value for m in HVACMode} - {HVACMode.OFF.value}


class RawTimingsCommand(Command):
    """An IR command carrying precomputed raw timings."""

    def __init__(self, timings: list[int], modulation: int) -> None:
        super().__init__(modulation=modulation, repeat_count=0)
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        return self._timings


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Create the climate entity for a config entry."""
    maps = await async_get_code_maps(hass)
    code_map = maps.get(entry.data[CONF_CODE_MAP])
    if code_map is None:
        raise HomeAssistantError(f"IR code map '{entry.data[CONF_CODE_MAP]}' is missing")
    # Options replace the device settings wholesale, so a cleared optional field stays cleared.
    config = dict(entry.data)
    if entry.options:
        config = {CONF_NAME: entry.data[CONF_NAME], CONF_CODE_MAP: entry.data[CONF_CODE_MAP], **entry.options}
    async_add_entities([IrAcClimate(entry, code_map, config)])


class IrAcClimate(InfraredEmitterConsumerEntity, ClimateEntity, RestoreEntity):
    """An air conditioner driven by IR frames built from a code map."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_translation_key = "ir_ac"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, entry: ConfigEntry, code_map: CodeMap, config: dict[str, Any]) -> None:
        self._map = code_map
        self._infrared_emitter_entity_id = config[CONF_EMITTER]
        self._receiver_entity_id: str | None = config.get(CONF_RECEIVER)
        self._temperature_sensor: str | None = config.get(CONF_TEMPERATURE_SENSOR)
        self._humidity_sensor: str | None = config.get(CONF_HUMIDITY_SENSOR)
        self._mirror_target: bool = config.get(CONF_MIRROR_TARGET, True)
        enabled = set(config.get(CONF_FEATURES, [n for n, f in code_map.fields.items() if f.default_enabled]))

        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=config[CONF_NAME],
            manufacturer=code_map.manufacturer or None,
            model=code_map.name,
        )

        modes = [m for m in code_map.fields[FIELD_MODE].names() if m in _VALID_MODES]
        self._attr_hvac_modes = [HVACMode.OFF, *(HVACMode(m) for m in modes)]
        temp = code_map.fields[FIELD_TEMPERATURE]
        self._attr_min_temp = temp.minimum if temp.minimum is not None else 16
        self._attr_max_temp = temp.maximum if temp.maximum is not None else 30
        self._attr_target_temperature_step = temp.step

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._has_fan = FIELD_FAN in code_map.fields
        self._has_swing = FIELD_SWING in code_map.fields and (
            not code_map.fields[FIELD_SWING].optional or FIELD_SWING in enabled
        )
        self._has_swing_h = FIELD_SWING_HORIZONTAL in code_map.fields and (
            not code_map.fields[FIELD_SWING_HORIZONTAL].optional or FIELD_SWING_HORIZONTAL in enabled
        )
        if self._has_fan:
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = code_map.fields[FIELD_FAN].names()
        if self._has_swing:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = code_map.fields[FIELD_SWING].names()
        if self._has_swing_h:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
            self._attr_swing_horizontal_modes = code_map.fields[FIELD_SWING_HORIZONTAL].names()
        self._attr_supported_features = features

        # Current settings. hvac_mode OFF is represented by power=off; the mode
        # the unit returns to on power-up is kept separately.
        defaults = code_map.defaults
        self._attr_hvac_mode = HVACMode.OFF
        self._last_mode: str = "cool" if "cool" in modes else modes[0]
        self._attr_target_temperature = float(
            defaults.get(FIELD_TEMPERATURE, min(max(22, self._attr_min_temp), self._attr_max_temp))
        )
        self._attr_fan_mode = defaults.get(FIELD_FAN, self._attr_fan_modes[0]) if self._has_fan else None
        self._attr_swing_mode = defaults.get(FIELD_SWING, "off") if self._has_swing else None
        self._attr_swing_horizontal_mode = (
            defaults.get(FIELD_SWING_HORIZONTAL, "off") if self._has_swing_h else None
        )

        self._pending_send: CALLBACK_TYPE | None = None
        self._last_sent: tuple[tuple[str, Any], ...] | None = None
        self._last_sent_at = 0.0
        self._unsub_receiver: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------ lifecycle
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore()

        tracked = [e for e in (self._temperature_sensor, self._humidity_sensor) if e]
        if tracked:
            self._update_sensors()
            self.async_on_remove(async_track_state_change_event(self.hass, tracked, self._sensor_changed))

        if self._receiver_entity_id:
            self._subscribe_receiver()
            self.async_on_remove(
                async_track_state_change_event(self.hass, [self._receiver_entity_id], self._receiver_state_changed)
            )
            self.async_on_remove(self._unsubscribe_receiver)
        self.async_on_remove(self._cancel_pending_send)

    async def _async_restore(self) -> None:
        if (last := await self.async_get_last_state()) is None:
            return
        attrs = last.attributes
        if last.state in (m.value for m in self._attr_hvac_modes):
            self._attr_hvac_mode = HVACMode(last.state)
        if (t := attrs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(t)
        if self._has_fan and attrs.get("fan_mode") in (self._attr_fan_modes or []):
            self._attr_fan_mode = attrs["fan_mode"]
        if self._has_swing and attrs.get("swing_mode") in (self._attr_swing_modes or []):
            self._attr_swing_mode = attrs["swing_mode"]
        if self._has_swing_h and attrs.get("swing_horizontal_mode") in (self._attr_swing_horizontal_modes or []):
            self._attr_swing_horizontal_mode = attrs["swing_horizontal_mode"]
        if (extra := await self.async_get_last_extra_data()) is not None:
            mode = extra.as_dict().get("last_mode")
            if mode in self._map.fields[FIELD_MODE].names():
                self._last_mode = mode
        if self._attr_hvac_mode != HVACMode.OFF:
            self._last_mode = self._attr_hvac_mode.value

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return RestoredExtraData({"last_mode": self._last_mode})

    # ------------------------------------------------------------- services
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.OFF:
            self._last_mode = hvac_mode.value
        self._attr_hvac_mode = hvac_mode
        self._changed()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode(self._last_mode))

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            if mode != HVACMode.OFF:
                self._last_mode = HVACMode(mode).value
            self._attr_hvac_mode = HVACMode(mode)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(temp)
        self._changed()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode
        self._changed()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self._attr_swing_mode = swing_mode
        self._changed()

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        self._attr_swing_horizontal_mode = swing_horizontal_mode
        self._changed()

    # -------------------------------------------------------------- sending
    def _state_dict(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            FIELD_POWER: "off" if self._attr_hvac_mode == HVACMode.OFF else "on",
            FIELD_MODE: self._last_mode if self._attr_hvac_mode == HVACMode.OFF else self._attr_hvac_mode.value,
            FIELD_TEMPERATURE: self._attr_target_temperature,
        }
        if self._has_fan:
            state[FIELD_FAN] = self._attr_fan_mode
        if self._has_swing:
            state[FIELD_SWING] = self._attr_swing_mode
        if self._has_swing_h:
            state[FIELD_SWING_HORIZONTAL] = self._attr_swing_horizontal_mode
        return state

    @callback
    def _changed(self) -> None:
        """Show the new state immediately and send it after a short settle delay."""
        self.async_write_ha_state()
        self._cancel_pending_send()
        self._pending_send = async_call_later(self.hass, SEND_DELAY, self._send_now)

    @callback
    def _cancel_pending_send(self) -> None:
        if self._pending_send is not None:
            self._pending_send()
            self._pending_send = None

    async def _send_now(self, _now: Any = None) -> None:
        self._pending_send = None
        state = self._state_dict()
        try:
            frame = self._map.encode(state)
        except ValueError as err:
            _LOGGER.error("%s: cannot encode %s: %s", self.entity_id, state, err)
            return
        _LOGGER.debug("%s sending %s -> %s", self.entity_id, state, " ".join(f"{b:02X}" for b in frame))
        self._last_sent = self._comparable(state)
        self._last_sent_at = time.monotonic()
        try:
            await self._send_command(RawTimingsCommand(self._map.timings(frame), self._map.carrier))
        except HomeAssistantError as err:
            _LOGGER.error("%s: failed to send IR command: %s", self.entity_id, err)

    def _comparable(self, state: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
        """Normalise a state dict so a sent state and a decoded echo compare equal."""
        out = dict(state)
        out[FIELD_TEMPERATURE] = int(round(float(out[FIELD_TEMPERATURE])))
        return tuple(sorted((k, v) for k, v in out.items() if k in self._state_dict()))

    # ------------------------------------------------------------ receiving
    @callback
    def _subscribe_receiver(self) -> None:
        if self._unsub_receiver is not None or not self._receiver_entity_id:
            return
        try:
            self._unsub_receiver = async_subscribe_receiver(self.hass, self._receiver_entity_id, self._handle_signal)
            _LOGGER.debug("%s listening on %s", self.entity_id, self._receiver_entity_id)
        except HomeAssistantError:
            # Receiver not loaded yet; retried when its state changes.
            self._unsub_receiver = None

    @callback
    def _unsubscribe_receiver(self) -> None:
        if self._unsub_receiver is not None:
            self._unsub_receiver()
            self._unsub_receiver = None

    @callback
    def _receiver_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or new.state == STATE_UNAVAILABLE:
            self._unsubscribe_receiver()
            return
        # Re-subscribe after the receiver comes back: the entity object may have been recreated.
        if old is None or old.state == STATE_UNAVAILABLE:
            self._unsubscribe_receiver()
        self._subscribe_receiver()

    @callback
    def _handle_signal(self, signal: InfraredReceivedSignal) -> None:
        decoded = self._map.decode(signal.timings)
        if decoded is None:
            return
        comparable = self._comparable({k: decoded.get(k) for k in self._state_dict()})
        if comparable == self._last_sent and time.monotonic() - self._last_sent_at < ECHO_WINDOW:
            return  # our own transmission bouncing back
        _LOGGER.debug("%s remote frame decoded: %s", self.entity_id, decoded)

        mode = decoded.get(FIELD_MODE)
        if mode in self._map.fields[FIELD_MODE].names() and mode in _VALID_MODES:
            self._last_mode = mode
        if decoded.get(FIELD_POWER) == "off":
            self._attr_hvac_mode = HVACMode.OFF
        elif decoded.get(FIELD_POWER) == "on" and mode in _VALID_MODES:
            self._attr_hvac_mode = HVACMode(mode)
        if (temp := decoded.get(FIELD_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(temp)
        if self._has_fan and decoded.get(FIELD_FAN) in (self._attr_fan_modes or []):
            self._attr_fan_mode = decoded[FIELD_FAN]
        if self._has_swing and decoded.get(FIELD_SWING) in (self._attr_swing_modes or []):
            self._attr_swing_mode = decoded[FIELD_SWING]
        if self._has_swing_h and decoded.get(FIELD_SWING_HORIZONTAL) in (self._attr_swing_horizontal_modes or []):
            self._attr_swing_horizontal_mode = decoded[FIELD_SWING_HORIZONTAL]

        # A remote press supersedes anything still queued from HA.
        self._cancel_pending_send()
        self.async_write_ha_state()
        self.hass.bus.async_fire(EVENT_REMOTE_COMMAND, {"entity_id": self.entity_id, **decoded})

    # -------------------------------------------------------------- sensors
    @property
    def current_temperature(self) -> float | None:
        # Without a room reading, optionally mirror the set point; HomeKit would otherwise show 21 °C.
        if self._attr_current_temperature is None and self._mirror_target:
            return self._attr_target_temperature
        return self._attr_current_temperature

    @callback
    def _sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        self._update_sensors()
        self.async_write_ha_state()

    @callback
    def _update_sensors(self) -> None:
        self._attr_current_temperature = self._read_float(self._temperature_sensor)
        self._attr_current_humidity = self._read_float(self._humidity_sensor)

    def _read_float(self, entity_id: str | None) -> float | None:
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

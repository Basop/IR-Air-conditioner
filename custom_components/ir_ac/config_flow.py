"""Config flow for IR Air Conditioner: one entry per air-conditioner unit."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .codec import CodeMap
from .const import (
    CONF_CODE_MAP,
    CONF_EMITTER,
    CONF_FEATURES,
    CONF_HUMIDITY_SENSOR,
    CONF_RECEIVER,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)
from .maps import async_get_code_maps


def _map_label(code_map: CodeMap) -> str:
    label = f"{code_map.name} - {code_map.models}" if code_map.models else code_map.name
    return label if code_map.tested else f"{label} (untested)"


def _devices_schema(code_map: CodeMap, current: dict[str, Any]) -> vol.Schema:
    """Emitter/receiver/sensor/feature form, pre-filled from ``current``."""
    optional_fields = [name for name, f in code_map.fields.items() if f.optional]
    default_features = current.get(
        CONF_FEATURES,
        [name for name in optional_fields if code_map.fields[name].default_enabled],
    )

    def suggested(key: str) -> dict[str, Any]:
        return {"suggested_value": current[key]} if current.get(key) else {}

    schema: dict[Any, Any] = {
        vol.Required(CONF_EMITTER, default=current.get(CONF_EMITTER, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="infrared", device_class="emitter")
        ),
        vol.Optional(CONF_RECEIVER, description=suggested(CONF_RECEIVER)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="infrared", device_class="receiver")
        ),
        vol.Optional(CONF_TEMPERATURE_SENSOR, description=suggested(CONF_TEMPERATURE_SENSOR)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_HUMIDITY_SENSOR, description=suggested(CONF_HUMIDITY_SENSOR)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
        ),
    }
    if optional_fields:
        schema[vol.Optional(CONF_FEATURES, default=default_features)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=optional_fields,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key="features",
            )
        )
    return vol.Schema(schema)


class IrAcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an IR-controlled air conditioner."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the unit and pick its remote's code map."""
        maps = await async_get_code_maps(self.hass, reload=True)
        if not maps:
            return self.async_abort(reason="no_code_maps")
        if user_input is not None:
            self._data = dict(user_input)
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Air Conditioner"): selector.TextSelector(),
                    vol.Required(CONF_CODE_MAP, default=next(iter(maps))): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=map_id, label=_map_label(m))
                                for map_id, m in maps.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the IR emitter, optional receiver and room sensors."""
        maps = await async_get_code_maps(self.hass)
        code_map = maps[self._data[CONF_CODE_MAP]]
        if user_input is not None:
            return self.async_create_entry(title=self._data[CONF_NAME], data={**self._data, **user_input})
        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(code_map, {}),
            description_placeholders={"code_map": _map_label(code_map)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IrAcOptionsFlow()


class IrAcOptionsFlow(OptionsFlow):
    """Change the emitter, receiver, sensors or enabled features of a unit."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        maps = await async_get_code_maps(self.hass)
        current = {**self.config_entry.data, **self.config_entry.options}
        code_map = maps.get(current[CONF_CODE_MAP])
        if code_map is None:
            return self.async_abort(reason="code_map_missing")
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=_devices_schema(code_map, current))

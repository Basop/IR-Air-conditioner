# IR Air Conditioner

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/Basop/IR-Air-conditioner/actions/workflows/validate.yml/badge.svg)](https://github.com/Basop/IR-Air-conditioner/actions/workflows/validate.yml)

A Home Assistant integration that turns an IR-remote air conditioner into a climate
entity. Commands are sent through Home Assistant `infrared` emitter entities, such as an
[ESPHome IR proxy](https://github.com/esphome/infrared-proxies), so each unit can use its
own proxy.

- One config entry per indoor unit, each with its own IR transmitter
- Optional IR receiver: presses on the unit's own remote are decoded and the entity
  follows them (fires an `ir_ac_remote_command` event too)
- Optional room temperature/humidity sensors shown as the current temperature/humidity
- Without a room sensor, the set temperature can be shown as the current temperature, so
  HomeKit doesn't fall back to 21 °C (on by default)
- Changes made in quick succession (e.g. Apple Home setting mode + temperature) are
  merged into one transmission
- Remote protocols are JSON code maps; add your own without changing the code

## Supported remotes

| Code map | Units | Status |
|---|---|---|
| Mitsubishi Heavy 88-bit (ZJS) | SRK..ZS/ZSP wall units, e.g. SRK25ZSP-W1 (remote RLA502A700 family) | Tested |
| Mitsubishi Heavy 152-bit (ZMS) | SRK..ZM/ZMP/ZMX wall units (remote RKX502A001 family) | Untested |

Other remotes can be added as code maps, see
[`custom_components/ir_ac/maps/README.md`](custom_components/ir_ac/maps/README.md).

## Requirements

- Home Assistant 2026.9 or newer (uses the built-in `infrared` integration)
- An `infrared` emitter entity per unit, e.g. an ESPHome IR proxy in the same room

## Installation

### HACS

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Basop/IR-Air-conditioner` with type **Integration**.
3. Install **IR Air Conditioner** and restart Home Assistant.

### Manual

Copy `custom_components/ir_ac` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → IR Air Conditioner**, once per unit:

1. Name the unit and pick the code map for its remote.
2. Pick the IR transmitter the unit should be controlled through, and optionally the
   IR receiver and room temperature/humidity sensors.
3. Choose which optional features (vertical vane, horizontal louvre) the unit has.

Change these later with **Configure** on the entry.

## Custom code maps

Put a JSON file in `/config/ir_ac_maps/` (it appears as `user_<filename>` in the setup
dialog). Start from a copy of
[`mhi_88.json`](custom_components/ir_ac/maps/mhi_88.json); the format is described in
[`maps/README.md`](custom_components/ir_ac/maps/README.md).

## Notes

Air conditioners don't report their state over IR, so the entity is optimistic
(assumed state). If someone uses the physical remote and no receiver is configured, Home
Assistant won't know about the change.

### Current temperature without a room sensor

HomeKit always shows a current temperature; if the entity has none, it shows 21 °C. With
**Show set temperature as current temperature when no room sensor reading** enabled (the
default), the entity reports its set temperature as the current temperature whenever no
room sensor is configured or the sensor is unavailable. This applies to the entity
itself, so Home Assistant cards show it too. Configure a room temperature sensor to show
the real room temperature instead.

The setting only changes what is displayed. The integration never switches the unit on or
off based on temperature; the air conditioner's own thermostat still regulates the room.

# IR air-conditioner code maps

Each `.json` file here is one remote protocol. To add your own without touching the
integration, put a file in `/config/ir_ac_maps/` (shown as `user_<filename>` in the
setup dialog) and add the unit again. Copy `mhi_88.json` as a starting point.

| Key | Meaning |
|---|---|
| `template` | The whole frame in hex with every field at 0, signature bytes included |
| `signature_length` | Leading bytes that must match `template` when decoding |
| `checksum` | `{"type": "inverted_pairs", "start": N}` (byte N+1 = ~byte N, N+3 = ~N+2, ...) or `{"type": "none"}` |
| `bit_order` | `lsb` or `msb` (order bits are sent within each byte) |
| `timing` | `header_mark`, `header_space`, `bit_mark`, `one_space`, `zero_space` (µs), optional `footer_mark` |
| `carrier_hz` | Usually 38000 |
| `fields` | Settings as bit slices, see below |
| `defaults` | Values used before HA knows better (e.g. `"fan": "auto"`) |

A field is `{"bits": [[byte, start_bit, length], ...], ...}` with the slices listed
least-significant part first (bit 0 = LSB of the byte). Named fields add
`"values": {"name": raw}`; numeric fields use `offset` (raw = value - offset), `min`,
`max` and `step`. `"optional": true` lets a unit switch the field off in its settings;
`"default_enabled": false` leaves it off unless ticked.

Field names the climate entity uses:

* `power` (required): values `on`/`off`
* `mode` (required): values named after HA HVAC modes: `auto`, `cool`, `dry`, `fan_only`, `heat`, `heat_cool`
* `temperature` (required, numeric)
* `fan`: any names (`auto`, `low`, `medium`, `high`, `powerful`, `econo` have UI labels)
* `swing`: vertical vane; `on` = swinging, `off` = stop, positions `highest` ... `lowest`
* `swing_horizontal`: horizontal louvre, same idea

To work out a new remote: add a `remote_receiver` with `dump: raw` to the IR proxy,
press buttons one change at a time, and compare the decoded bytes.

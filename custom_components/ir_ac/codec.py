"""Declarative IR air-conditioner code maps: encode a state to raw timings, decode back.

A code map (JSON) describes one remote protocol as a fixed-length byte frame:

* ``template``   - the frame with all fields at zero (signature bytes included)
* ``checksum``   - how integrity bytes are derived (``inverted_pairs`` or ``none``)
* ``fields``     - each setting as bit slices of the frame plus a value table
* ``timing``     - pulse-distance timings (header, bit mark, one/zero spaces)

This module has no Home Assistant dependencies so maps can be tested standalone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fields the climate entity understands. Anything else in a map is carried along
# at its default value (useful for bits a unit needs but HA never changes).
FIELD_POWER = "power"
FIELD_MODE = "mode"
FIELD_TEMPERATURE = "temperature"
FIELD_FAN = "fan"
FIELD_SWING = "swing"
FIELD_SWING_HORIZONTAL = "swing_horizontal"

HEADER_TOLERANCE = 0.3


class CodeMapError(ValueError):
    """Raised when a code map file is invalid."""


@dataclass(frozen=True, slots=True)
class BitSlice:
    """``length`` bits of ``byte`` starting at bit ``start`` (bit 0 = LSB)."""

    byte: int
    start: int
    length: int


@dataclass(slots=True)
class Field:
    """One setting in the frame, possibly split over several bit slices."""

    name: str
    slices: list[BitSlice]  # least-significant part first
    values: dict[str, int] | None = None  # named values; None for numeric fields
    offset: int = 0  # numeric fields: encoded = value - offset
    minimum: int | None = None
    maximum: int | None = None
    step: float = 1
    optional: bool = False  # can be disabled per unit in the config entry
    default_enabled: bool = True

    @property
    def width(self) -> int:
        return sum(s.length for s in self.slices)

    def names(self) -> list[str]:
        return list(self.values or {})

    def name_for(self, raw: int) -> str | None:
        for name, value in (self.values or {}).items():
            if value == raw:
                return name
        return None


@dataclass(slots=True)
class CodeMap:
    """A parsed code map."""

    map_id: str
    name: str
    manufacturer: str
    models: str
    template: list[int]
    fields: dict[str, Field]
    defaults: dict[str, Any]
    header_mark: int
    header_space: int
    bit_mark: int
    one_space: int
    zero_space: int
    footer_mark: int
    carrier: int = 38000
    lsb_first: bool = True
    checksum: dict[str, Any] = field(default_factory=lambda: {"type": "none"})
    signature_length: int = 0
    tested: bool = False
    notes: str = ""

    # ---------------------------------------------------------------- parsing
    @classmethod
    def from_dict(cls, map_id: str, data: dict[str, Any]) -> CodeMap:
        try:
            template = [int(b, 16) for b in data["template"].split()]
            timing = data["timing"]
            fields: dict[str, Field] = {}
            for name, spec in data["fields"].items():
                slices = [BitSlice(*part) for part in spec["bits"]]
                for s in slices:
                    if not (0 <= s.byte < len(template) and 0 <= s.start and s.start + s.length <= 8):
                        raise CodeMapError(f"{map_id}: field {name} has an out-of-range bit slice {s}")
                fields[name] = Field(
                    name=name,
                    slices=slices,
                    values=spec.get("values"),
                    offset=spec.get("offset", 0),
                    minimum=spec.get("min"),
                    maximum=spec.get("max"),
                    step=spec.get("step", 1),
                    optional=spec.get("optional", False),
                    default_enabled=spec.get("default_enabled", True),
                )
            for required in (FIELD_POWER, FIELD_MODE, FIELD_TEMPERATURE):
                if required not in fields:
                    raise CodeMapError(f"{map_id}: missing required field '{required}'")
            return cls(
                map_id=map_id,
                name=data["name"],
                manufacturer=data.get("manufacturer", ""),
                models=data.get("models", ""),
                template=template,
                fields=fields,
                defaults=data.get("defaults", {}),
                header_mark=timing["header_mark"],
                header_space=timing["header_space"],
                bit_mark=timing["bit_mark"],
                one_space=timing["one_space"],
                zero_space=timing["zero_space"],
                footer_mark=timing.get("footer_mark", timing["bit_mark"]),
                carrier=data.get("carrier_hz", 38000),
                lsb_first=data.get("bit_order", "lsb") == "lsb",
                checksum=data.get("checksum", {"type": "none"}),
                signature_length=data.get("signature_length", 0),
                tested=data.get("tested", False),
                notes=data.get("notes", ""),
            )
        except CodeMapError:
            raise
        except (KeyError, TypeError, ValueError) as err:
            raise CodeMapError(f"{map_id}: invalid code map ({err!r})") from err

    # ------------------------------------------------------------- bit access
    @staticmethod
    def _get(frame: list[int], f: Field) -> int:
        value, shift = 0, 0
        for s in f.slices:
            part = (frame[s.byte] >> s.start) & ((1 << s.length) - 1)
            value |= part << shift
            shift += s.length
        return value

    @staticmethod
    def _set(frame: list[int], f: Field, value: int) -> None:
        if value < 0 or value >= 1 << f.width:
            raise ValueError(f"value {value} does not fit field {f.name}")
        for s in f.slices:
            mask = ((1 << s.length) - 1) << s.start
            frame[s.byte] = (frame[s.byte] & ~mask) | ((value << s.start) & mask)
            value >>= s.length

    def _apply_checksum(self, frame: list[int]) -> None:
        kind = self.checksum.get("type", "none")
        if kind == "inverted_pairs":
            for i in range(self.checksum.get("start", 0), len(frame) - 1, 2):
                frame[i + 1] = frame[i] ^ 0xFF
        elif kind != "none":
            raise CodeMapError(f"{self.map_id}: unknown checksum type {kind}")

    def _checksum_ok(self, frame: list[int]) -> bool:
        kind = self.checksum.get("type", "none")
        if kind == "inverted_pairs":
            return all(
                frame[i] ^ frame[i + 1] == 0xFF
                for i in range(self.checksum.get("start", 0), len(frame) - 1, 2)
            )
        return True

    # --------------------------------------------------------------- encoding
    def encode(self, state: dict[str, Any]) -> list[int]:
        """Build the frame bytes for a state dict (field name -> name or number)."""
        frame = list(self.template)
        for name, f in self.fields.items():
            value = state.get(name, self.defaults.get(name))
            if value is None:
                continue
            if f.values is not None:
                if isinstance(value, bool):
                    value = "on" if value else "off"
                if value not in f.values:
                    raise ValueError(f"'{value}' is not a valid {name} for {self.map_id}")
                raw = f.values[value]
            else:
                number = int(round(float(value)))
                if f.minimum is not None:
                    number = max(number, f.minimum)
                if f.maximum is not None:
                    number = min(number, f.maximum)
                raw = number - f.offset
            self._set(frame, f, raw)
        self._apply_checksum(frame)
        return frame

    def timings(self, frame: list[int]) -> list[int]:
        """Raw timings (µs, +mark / -space) for a frame."""
        out = [self.header_mark, -self.header_space]
        for byte in frame:
            for i in range(8):
                bit = (byte >> i) & 1 if self.lsb_first else (byte >> (7 - i)) & 1
                out += [self.bit_mark, -(self.one_space if bit else self.zero_space)]
        out.append(self.footer_mark)
        return out

    # --------------------------------------------------------------- decoding
    def decode(self, timings: list[int]) -> dict[str, Any] | None:
        """Find and decode a valid frame in received timings; None if there isn't one."""
        nbits = len(self.template) * 8
        threshold = (self.one_space + self.zero_space) / 2
        one_is_short = self.one_space < self.zero_space

        def near(value: int, target: int) -> bool:
            return abs(abs(value) - target) <= target * HEADER_TOLERANCE

        for start in range(len(timings) - 2 * nbits - 1):
            if not (timings[start] > 0 and timings[start + 1] < 0):
                continue
            if not (near(timings[start], self.header_mark) and near(timings[start + 1], self.header_space)):
                continue
            spaces = timings[start + 3 : start + 3 + 2 * nbits : 2]
            if len(spaces) < nbits or any(s >= 0 for s in spaces):
                continue
            frame = [0] * len(self.template)
            for i, space in enumerate(spaces):
                bit = (abs(space) < threshold) == one_is_short
                if bit:
                    byte, pos = divmod(i, 8)
                    frame[byte] |= 1 << (pos if self.lsb_first else 7 - pos)
            if frame[: self.signature_length] != self.template[: self.signature_length]:
                continue
            if not self._checksum_ok(frame):
                continue
            return self.parse(frame)
        return None

    def parse(self, frame: list[int]) -> dict[str, Any]:
        """Turn frame bytes into a state dict. Unknown named values come back as None."""
        state: dict[str, Any] = {}
        for name, f in self.fields.items():
            raw = self._get(frame, f)
            state[name] = f.name_for(raw) if f.values is not None else raw + f.offset
        return state

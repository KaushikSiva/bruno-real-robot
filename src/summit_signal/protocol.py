"""Bounded newline-delimited protocol from the Mac operator to the Jetson."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

MAX_GOAL_BYTES = 512
MAX_SEQUENCE = 2**63 - 1
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
GOAL_KEYS = {
    "schema_version",
    "session_id",
    "sequence",
    "sent_monotonic_s",
    "axes",
    "deadman",
    "emergency_stop",
    "quit",
    "connected",
}


class ProtocolError(ValueError):
    """Raised when an operator goal is malformed or outside its wire bounds."""


@dataclass(frozen=True)
class ArmGoal:
    session_id: str
    sequence: int
    sent_monotonic_s: float
    axes: tuple[float, float, float]
    deadman: bool
    emergency_stop: bool
    quit: bool
    connected: bool

    def __post_init__(self) -> None:
        if not SESSION_PATTERN.fullmatch(self.session_id):
            raise ProtocolError("session_id must contain 8-64 safe characters")
        if type(self.sequence) is not int or not 0 <= self.sequence <= MAX_SEQUENCE:
            raise ProtocolError("sequence must be a non-negative 63-bit integer")
        if not math.isfinite(self.sent_monotonic_s) or self.sent_monotonic_s < 0.0:
            raise ProtocolError("sent_monotonic_s must be finite and non-negative")
        if len(self.axes) != 3:
            raise ProtocolError("axes must contain exactly three values")
        if any(not math.isfinite(value) or not -1.0 <= value <= 1.0 for value in self.axes):
            raise ProtocolError("axes must be finite normalized values")
        if any(type(value) is not bool for value in self.flags):
            raise ProtocolError("goal flags must be booleans")
        if not self.connected and not self.emergency_stop:
            raise ProtocolError("a disconnected goal must request emergency stop")

    @property
    def flags(self) -> tuple[bool, bool, bool, bool]:
        return self.deadman, self.emergency_stop, self.quit, self.connected

    def encode(self) -> bytes:
        payload = {
            "schema_version": 1,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "sent_monotonic_s": self.sent_monotonic_s,
            "axes": list(self.axes),
            "deadman": self.deadman,
            "emergency_stop": self.emergency_stop,
            "quit": self.quit,
            "connected": self.connected,
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_GOAL_BYTES:
            raise ProtocolError("encoded goal exceeds the wire-size limit")
        return encoded

    @classmethod
    def decode(cls, line: bytes | str) -> ArmGoal:
        encoded = line.encode() if isinstance(line, str) else line
        if not encoded or len(encoded) > MAX_GOAL_BYTES:
            raise ProtocolError("goal line is empty or exceeds the wire-size limit")
        try:
            raw: Any = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError("goal line is not valid UTF-8 JSON") from error
        if not isinstance(raw, dict) or set(raw) != GOAL_KEYS:
            raise ProtocolError("goal object has missing or unexpected fields")
        if raw["schema_version"] != 1:
            raise ProtocolError("unsupported goal schema")
        axes = raw["axes"]
        if not isinstance(axes, list) or len(axes) != 3:
            raise ProtocolError("axes must be a three-element array")
        if any(isinstance(value, bool) or not isinstance(value, int | float) for value in axes):
            raise ProtocolError("axis values must be numbers")
        if isinstance(raw["sent_monotonic_s"], bool) or not isinstance(
            raw["sent_monotonic_s"], int | float
        ):
            raise ProtocolError("sent_monotonic_s must be a number")
        return cls(
            session_id=raw["session_id"],
            sequence=raw["sequence"],
            sent_monotonic_s=float(raw["sent_monotonic_s"]),
            axes=tuple(float(value) for value in axes),
            deadman=raw["deadman"],
            emergency_stop=raw["emergency_stop"],
            quit=raw["quit"],
            connected=raw["connected"],
        )

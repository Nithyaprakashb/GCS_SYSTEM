"""MAVLink transports.

One shared transport can carry multiple vehicles (validated multipoint topology);
the independent topology creates one point-to-point transport per aircraft.  No
transport is created or reported as connected merely because a COM port exists.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

try:  # pymavlink is required for REAL mode, optional for pure simulation/testing
    from pymavlink import mavutil
except Exception:  # pragma: no cover - exercised only on stripped installs
    mavutil = None  # type: ignore[assignment]

MessageCallback = Callable[[Any], None]
StateCallback = Callable[[str, str], None]


class TransportError(RuntimeError):
    pass


class MavlinkTransport:
    """Serial or UDP/TCP MAVLink transport with a supervised reader thread."""

    def __init__(
        self,
        endpoint: str,
        *,
        baud: int = 57600,
        source_system: int = 255,
        on_message: MessageCallback,
        on_state: StateCallback,
        name: str = "transport",
    ) -> None:
        if mavutil is None:
            raise TransportError("pymavlink is not installed")
        self.endpoint = endpoint
        self.baud = baud
        self.source_system = source_system
        self.on_message = on_message
        self.on_state = on_state
        self.name = name
        self.conn: Any = None
        self.target_system: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error: str | None = None

    def open(self) -> None:
        try:
            if self.endpoint.startswith("udp") or self.endpoint.startswith("tcp"):
                self.conn = mavutil.mavlink_connection(
                    self.endpoint,
                    source_system=self.source_system,
                    source_component=190,
                    autoreconnect=True,
                )
            else:
                self.conn = mavutil.mavlink_connection(
                    self.endpoint,
                    baud=self.baud,
                    source_system=self.source_system,
                    source_component=190,
                    autoreconnect=True,
                )
            # Wait for the first heartbeat without blocking app startup forever.
            # wait_heartbeat returns None on timeout or when the link dies: a port that
            # merely opens is NOT a verified aircraft link, and reporting CONNECTED there
            # would let the operator command a vehicle that has never answered.
            heartbeat = self.conn.wait_heartbeat(timeout=3.0)
            if heartbeat is None:
                raise TransportError(
                    "Serial/network port opened but no MAVLink HEARTBEAT arrived within 3 s"
                )
            self.target_system = int(getattr(self.conn, "target_system", 0) or 0)
            self.connected = True
            self.on_state(self.name, "CONNECTED")
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            self.on_state(self.name, "ERROR")
            raise TransportError(f"{self.endpoint}: {exc}") from exc

    def start(self) -> None:
        self.open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, name=f"mavlink-{self.name}", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                message = self.conn.recv_match(blocking=True, timeout=0.5)
                if message is None:
                    continue
                self.on_message(message)
            except Exception as exc:
                self.last_error = str(exc)
                self.connected = False
                self.on_state(self.name, "RECONNECTING")
                time.sleep(1.0)
                try:
                    if self.conn:
                        self.conn.close()
                except Exception:
                    pass
                try:
                    self.open()
                except Exception:
                    time.sleep(2.0)

    def send(self, message: Any) -> None:
        if not self.conn or not self.connected:
            raise TransportError("transport has no verified MAVLink link")
        self.conn.mav.send(message)

    def command_long(
        self,
        target_system: int,
        target_component: int,
        command: int,
        params: list[float],
        *,
        confirmation: int = 0,
    ) -> None:
        if not self.conn or not self.connected:
            raise TransportError("transport has no verified MAVLink link")
        padded = (params + [0.0] * 7)[:7]
        self.conn.mav.command_long_send(
            target_system,
            target_component,
            command,
            confirmation,
            *padded,
        )

    def mission_count(self, target_system: int, target_component: int, count: int, mission_type: int = 0) -> None:
        self._require_link()
        self.conn.mav.mission_count_send(target_system, target_component, count, mission_type)

    def mission_request_int(self, target_system: int, target_component: int, seq: int, mission_type: int = 0) -> None:
        self._require_link()
        self.conn.mav.mission_request_int_send(target_system, target_component, seq, mission_type)

    def mission_request_list(self, target_system: int, target_component: int, mission_type: int = 0) -> None:
        self._require_link()
        self.conn.mav.mission_request_list_send(target_system, target_component, mission_type)

    def _require_link(self) -> None:
        """Mission and parameter traffic needs the same verified link as commands.

        A dropped link keeps the socket object alive; writing to it would look like a
        successful upload to the caller while nothing reaches the vehicle.
        """
        if not self.conn or not self.connected:
            raise TransportError("transport has no verified MAVLink link")

    def mission_item_int(
        self,
        target_system: int,
        target_component: int,
        seq: int,
        frame: int,
        command: int,
        current: int,
        autocontinue: int,
        p1: float,
        p2: float,
        p3: float,
        p4: float,
        lat: float,
        lon: float,
        alt: float,
        mission_type: int = 0,
    ) -> None:
        self._require_link()
        self.conn.mav.mission_item_int_send(
            target_system,
            target_component,
            seq,
            frame,
            command,
            current,
            autocontinue,
            p1,
            p2,
            p3,
            p4,
            int(round(lat * 1e7)),
            int(round(lon * 1e7)),
            alt,
            mission_type,
        )

    def set_mode(self, target_system: int, target_component: int, mode_id: int) -> None:
        self._require_link()
        self.conn.mav.set_mode_send(
            target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
        )

    def param_set(self, target_system: int, target_component: int, name: str, value: float) -> None:
        self._require_link()
        self.conn.mav.param_set_send(
            target_system,
            target_component,
            name.encode("ascii"),
            float(value),
            mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
        )

    def param_request_read(self, target_system: int, target_component: int, name: str) -> None:
        self._require_link()
        self.conn.mav.param_request_read_send(
            target_system, target_component, name.encode("ascii"), -1
        )

    def close(self) -> None:
        self._stop.set()
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.connected = False
        self.on_state(self.name, "DISCONNECTED")


def probe_serial_ports() -> list[dict[str, Any]]:
    """Enumerate serial ports without claiming any of them are RFD modems."""
    try:
        from serial.tools import list_ports  # type: ignore

        return [
            {
                "port": port.device,
                "description": port.description,
                "hwid": port.hwid,
                "vid": port.vid,
                "pid": port.pid,
                "serial_number": port.serial_number,
                "is_modem_candidate": bool(
                    port.vid in {0x0403, 0x10C4, 0x1A86, 0x2341}
                    or "CP210" in (port.description or "")
                    or "FT232" in (port.description or "")
                    or "CH340" in (port.description or "")
                ),
            }
            for port in list_ports.comports()
        ]
    except Exception:
        return []

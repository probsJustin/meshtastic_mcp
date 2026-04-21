"""MCP server exposing Meshtastic USB/serial devices as tools."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import meshtastic
import meshtastic.serial_interface
import meshtastic.util
from mcp.server.fastmcp import FastMCP
from pubsub import pub

mcp = FastMCP("meshtastic")


@dataclass
class Connection:
    interface: meshtastic.serial_interface.SerialInterface
    port: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


_conn: Connection | None = None
_conn_lock = threading.Lock()
_MAX_BUFFERED_MESSAGES = 500


def _on_receive(packet: dict[str, Any], interface: Any) -> None:
    conn = _conn
    if conn is None:
        return
    try:
        decoded = packet.get("decoded") or {}
        if decoded.get("portnum") != "TEXT_MESSAGE_APP":
            return
        msg = {
            "from": packet.get("fromId"),
            "to": packet.get("toId"),
            "channel": packet.get("channel", 0),
            "text": decoded.get("text", ""),
            "rx_time": packet.get("rxTime"),
            "rx_snr": packet.get("rxSnr"),
            "rx_rssi": packet.get("rxRssi"),
            "hop_limit": packet.get("hopLimit"),
            "id": packet.get("id"),
        }
        with conn.lock:
            conn.messages.append(msg)
            if len(conn.messages) > _MAX_BUFFERED_MESSAGES:
                del conn.messages[: len(conn.messages) - _MAX_BUFFERED_MESSAGES]
    except Exception:
        pass


def _require_conn() -> Connection:
    if _conn is None:
        raise RuntimeError(
            "No active Meshtastic connection. Call `connect` first "
            "(use `list_devices` to find a port)."
        )
    return _conn


@mcp.tool()
def list_devices() -> dict[str, Any]:
    """List USB serial ports that look like Meshtastic devices.

    Scans the host for serial ports whose USB VID/PID matches known
    Meshtastic-compatible hardware (ESP32, nRF52, RAK, Heltec, T-Beam, etc.).
    Returns an empty list if no device is plugged in.
    """
    try:
        ports = meshtastic.util.findPorts(True)
    except Exception as e:
        return {"ports": [], "error": str(e)}
    return {"ports": list(ports), "count": len(ports)}


@mcp.tool()
def connect(port: str | None = None) -> dict[str, Any]:
    """Open a serial connection to a Meshtastic device.

    If `port` is omitted, the library auto-detects the first matching
    serial port. Only one connection can be active at a time.
    """
    global _conn
    with _conn_lock:
        if _conn is not None:
            return {"status": "already_connected", "port": _conn.port}
        try:
            iface = meshtastic.serial_interface.SerialInterface(devPath=port)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        resolved_port = port or getattr(iface, "devPath", None) or "<auto>"
        _conn = Connection(interface=iface, port=resolved_port)
        pub.subscribe(_on_receive, "meshtastic.receive")
        return {"status": "connected", "port": resolved_port}


@mcp.tool()
def disconnect() -> dict[str, Any]:
    """Close the active Meshtastic connection."""
    global _conn
    with _conn_lock:
        if _conn is None:
            return {"status": "not_connected"}
        try:
            pub.unsubscribe(_on_receive, "meshtastic.receive")
        except Exception:
            pass
        try:
            _conn.interface.close()
        except Exception:
            pass
        port = _conn.port
        _conn = None
        return {"status": "disconnected", "port": port}


@mcp.tool()
def get_device_info() -> dict[str, Any]:
    """Return info about the locally connected node (hw model, firmware, ids)."""
    conn = _require_conn()
    my = conn.interface.getMyNodeInfo() or {}
    user = my.get("user", {}) if isinstance(my, dict) else {}
    return {
        "port": conn.port,
        "node_num": my.get("num") if isinstance(my, dict) else None,
        "node_id": user.get("id"),
        "long_name": user.get("longName"),
        "short_name": user.get("shortName"),
        "hw_model": user.get("hwModel"),
        "firmware_version": getattr(conn.interface, "firmwareVersion", None),
        "raw": my,
    }


@mcp.tool()
def get_nodes() -> dict[str, Any]:
    """List all nodes known to the connected device's mesh database."""
    conn = _require_conn()
    nodes = conn.interface.nodes or {}
    out: list[dict[str, Any]] = []
    for node_id, n in nodes.items():
        user = n.get("user", {}) or {}
        pos = n.get("position", {}) or {}
        metrics = n.get("deviceMetrics", {}) or {}
        out.append(
            {
                "id": node_id,
                "num": n.get("num"),
                "long_name": user.get("longName"),
                "short_name": user.get("shortName"),
                "hw_model": user.get("hwModel"),
                "last_heard": n.get("lastHeard"),
                "snr": n.get("snr"),
                "battery_level": metrics.get("batteryLevel"),
                "voltage": metrics.get("voltage"),
                "latitude": pos.get("latitude"),
                "longitude": pos.get("longitude"),
                "altitude": pos.get("altitude"),
            }
        )
    return {"nodes": out, "count": len(out)}


@mcp.tool()
def send_text(
    text: str,
    destination: str = "^all",
    channel: int = 0,
    want_ack: bool = False,
) -> dict[str, Any]:
    """Send a text message over the mesh.

    `destination` is a node id like `!abcd1234`, a numeric node num as string,
    or `^all` to broadcast on the given channel index.
    """
    conn = _require_conn()
    try:
        packet = conn.interface.sendText(
            text,
            destinationId=destination,
            wantAck=want_ack,
            channelIndex=channel,
        )
        return {
            "status": "sent",
            "packet_id": getattr(packet, "id", None),
            "to": destination,
            "channel": channel,
            "want_ack": want_ack,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_messages(limit: int = 50, clear: bool = False) -> dict[str, Any]:
    """Return buffered text messages received since `connect`.

    The buffer holds the most recent messages (capped at 500). Set
    `clear=True` to drain the buffer after reading.
    """
    conn = _require_conn()
    with conn.lock:
        msgs = list(conn.messages[-limit:]) if limit > 0 else list(conn.messages)
        if clear:
            conn.messages.clear()
    return {"messages": msgs, "count": len(msgs)}


@mcp.tool()
def get_channels() -> dict[str, Any]:
    """Return configured channels on the local node."""
    conn = _require_conn()
    channels: list[dict[str, Any]] = []
    local = getattr(conn.interface, "localNode", None)
    if local is None:
        return {"channels": [], "error": "localNode not available"}
    for i in range(8):
        try:
            ch = local.getChannelByChannelIndex(i)
        except Exception:
            continue
        if ch is None:
            continue
        settings = getattr(ch, "settings", None)
        channels.append(
            {
                "index": i,
                "role": str(getattr(ch, "role", "")),
                "name": getattr(settings, "name", "") if settings else "",
                "psk_set": bool(getattr(settings, "psk", b"")) if settings else False,
            }
        )
    return {"channels": channels}


@mcp.tool()
def get_position() -> dict[str, Any]:
    """Return the local node's last known GPS position (if any)."""
    conn = _require_conn()
    my = conn.interface.getMyNodeInfo() or {}
    pos = my.get("position", {}) if isinstance(my, dict) else {}
    return {
        "latitude": pos.get("latitude"),
        "longitude": pos.get("longitude"),
        "altitude": pos.get("altitude"),
        "time": pos.get("time"),
        "sats_in_view": pos.get("satsInView"),
    }


@mcp.tool()
def request_telemetry(destination: str = "^local") -> dict[str, Any]:
    """Request a telemetry update from a node (default: local node)."""
    conn = _require_conn()
    try:
        conn.interface.sendTelemetry(destinationId=destination)
        return {"status": "requested", "destination": destination}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def traceroute(destination: str, hop_limit: int = 7) -> dict[str, Any]:
    """Run a traceroute to the given node id. Results arrive asynchronously
    on the device log — this tool only triggers the request.
    """
    conn = _require_conn()
    try:
        conn.interface.sendTraceRoute(dest=destination, hopLimit=hop_limit)
        return {"status": "requested", "destination": destination, "hop_limit": hop_limit}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def reboot(seconds: int = 5) -> dict[str, Any]:
    """Schedule a device reboot after `seconds`."""
    conn = _require_conn()
    local = getattr(conn.interface, "localNode", None)
    if local is None:
        return {"status": "error", "error": "localNode not available"}
    try:
        local.reboot(seconds)
        return {"status": "reboot_scheduled", "seconds": seconds}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

# meshtastic-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives LLM agents (Claude Code, Claude Desktop, any MCP-compatible client) the ability to talk to [Meshtastic](https://meshtastic.org) LoRa radios plugged into your machine over USB.

With this server attached, an agent can:

- Discover Meshtastic-capable serial ports on the host
- Open a connection to a specific radio (or auto-detect the first one)
- Enumerate every node the radio has heard on the mesh — long/short name, hardware model, SNR, battery, GPS
- Send broadcast or direct text messages on any channel
- Read text messages received since the connection opened (buffered via pubsub)
- Request telemetry, run a traceroute, or reboot the radio

No cloud, no account, no MQTT bridge required — it drives the same USB serial interface the official `meshtastic` CLI uses.

## Status

Alpha. Built against `meshtastic>=2.5`. The author wrote this without a radio on the development machine, so the control paths (`connect`, `send_text`, `reboot`, etc.) are implemented from the library's documented API but have not been exercised end-to-end against hardware — please open an issue if something misbehaves.

## Requirements

- Python 3.10+
- A Meshtastic radio reachable over USB (ESP32, nRF52, RAK WisBlock, Heltec, T-Beam, T-Echo, etc.)
- On Linux, your user needs access to the serial device. The usual fix:
  ```bash
  sudo usermod -a -G dialout $USER   # or `uucp` on Arch
  # log out and back in
  ```
- On macOS the device shows up as `/dev/cu.usbserial-*` or `/dev/cu.usbmodem*`; no extra permissions needed.
- On Windows it appears as a `COM*` port.

## Install

```bash
git clone https://github.com/probsJustin/meshtastic_mcp.git
cd meshtastic_mcp
pip install -e .
```

This installs a console script called `meshtastic-mcp` that speaks MCP over stdio.

## Wire it up

### Claude Code

Add to `~/.claude.json` (or a project-local `.mcp.json`):

```json
{
  "mcpServers": {
    "meshtastic": {
      "command": "meshtastic-mcp"
    }
  }
}
```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "meshtastic": {
      "command": "meshtastic-mcp"
    }
  }
}
```

Restart the client and the Meshtastic tools will appear.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_devices` | Scan USB for Meshtastic-compatible serial ports. Safe to call with no device connected. |
| `connect(port?)` | Open a serial connection. Auto-detects if `port` is omitted. One active connection at a time. |
| `disconnect` | Close the active connection and release the port. |
| `get_device_info` | Local node id, long/short name, hardware model, firmware version. |
| `get_nodes` | Every node in the radio's database with name, hw model, SNR, battery, GPS. |
| `get_channels` | Configured channels on the local node (index, role, name, whether a PSK is set). |
| `get_position` | Last known GPS fix for the local node. |
| `send_text(text, destination?, channel?, want_ack?)` | Broadcast (`^all`) or direct message (`!nodeid`) on a channel. |
| `get_messages(limit?, clear?)` | Read buffered inbound text messages (ring buffer of 500). |
| `request_telemetry(destination?)` | Ask a node for a telemetry update. |
| `traceroute(destination, hop_limit?)` | Kick off a traceroute. Results arrive asynchronously in device logs. |
| `reboot(seconds?)` | Schedule a device reboot. |

The destination for `send_text`, `request_telemetry`, and `traceroute` accepts:
- `^all` — broadcast to the channel (default for `send_text`)
- `^local` — the connected radio itself
- `!abcd1234` — a hex node id
- A numeric node number as a string

## Example session

Once the server is registered, ask the agent things like:

- "What Meshtastic devices are plugged in?"
- "Connect to the first one and tell me who else is on the mesh."
- "Broadcast 'dinner at 7' on the primary channel."
- "Show me any messages that have come in while we were talking."
- "Run a traceroute to `!a1b2c3d4`."

## How inbound messages work

`connect` subscribes to the `meshtastic.receive` pubsub topic. Every packet whose `portnum` is `TEXT_MESSAGE_APP` is appended to an in-memory ring buffer (capped at 500 entries). Call `get_messages` to drain or peek at it. The buffer is per-process — restarting the MCP server clears it.

Non-text packets (position, telemetry, routing) are not buffered; use `get_nodes` / `get_position` for the latest state the radio itself is tracking.

## Development

The whole server is one file: `meshtastic_mcp/server.py`. It uses `FastMCP` from the official MCP Python SDK and delegates all hardware I/O to the `meshtastic` Python package. If you want to add a tool, write a function, decorate it with `@mcp.tool()`, and call `_require_conn()` at the top if it needs an open connection.

```bash
pip install -e .
meshtastic-mcp            # runs the stdio server; ctrl-c to stop
```

## License

MIT.

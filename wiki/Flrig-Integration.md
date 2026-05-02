# Flrig Integration

[Flrig](http://www.w1hkj.com/files/flrig/) is a free rig control program that communicates with your transceiver. POTA Hunter connects to Flrig via XML-RPC to read and control your radio in real time.

---

## What Flrig Integration Enables

| Feature | Description |
|---------|-------------|
| **Auto-tune** | Clicking a POTA spot tunes your radio to that frequency |
| **Frequency capture** | Logs the exact frequency from your radio at the moment you click LOG QSO |
| **Band / Mode capture** | Reads the current mode (USB, CW, FT8, etc.) from your radio |
| **Live S-meter** | Displays received signal strength in the status bar |
| **Power meter** | Shows transmit power |
| **PTT indicator** | Shows whether your radio is transmitting |

---

## Setup

### 1. Install and Configure Flrig

Download Flrig from [w1hkj.com](http://www.w1hkj.com/files/flrig/). Select your transceiver model in Flrig's configuration and confirm it is communicating correctly with your radio.

Flrig's default XML-RPC server settings are:
- **Host**: `127.0.0.1` (localhost)
- **Port**: `12345`

These should work without changes for most setups.

### 2. Configure POTA Hunter

Open **Settings → Flrig Settings** and confirm:

| Setting | Default | Notes |
|---------|---------|-------|
| **Host** | `127.0.0.1` | Change only if Flrig is on a different machine |
| **Port** | `12345` | Must match Flrig's XML-RPC port setting |

Click **Test Connection** — POTA Hunter will report your current frequency if the connection is working.

### 3. Start Order

Start Flrig **before** starting POTA Hunter. POTA Hunter polls Flrig approximately once per second; if Flrig is not running at startup, the connection status in the header will show as offline. The connection is attempted automatically and will reconnect if Flrig is restarted.

---

## Running Without Flrig

Flrig is entirely optional. If Flrig is not running or not configured:

- Frequency, band, and mode fields in the log form will be blank — enter them manually.
- Clicking a POTA spot will still pre-fill the callsign, park, and grid fields, but will not tune your radio.
- The S-meter and power meter displays will not be active.

---

## Remote Rig Control (Network)

If Flrig is running on a different computer on your local network, change the **Host** in **Settings → Flrig Settings** to that computer's IP address. Ensure the remote machine's firewall allows inbound TCP connections on port 12345.

---

## Supported XML-RPC Methods

POTA Hunter uses the following Flrig XML-RPC methods:

| Method | Used for |
|--------|----------|
| `rig.get_vfo()` | Read current frequency (Hz) |
| `rig.get_mode()` | Read current mode |
| `rig.get_smeter()` | Read S-meter value |
| `rig.get_pwrmeter()` | Read power meter value |
| `rig.get_ptt()` | Read PTT state |
| `rig.set_vfo(freq)` | Tune to a new frequency |

All transceiver models supported by Flrig are compatible with POTA Hunter.

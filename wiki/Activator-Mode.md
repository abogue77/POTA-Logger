# Activator Mode

Activator Mode is for operators who are **at a POTA park** and want to post their own activation spot so other hunters can find them.

---

## Switching to Activator Mode

Click the **Activator Mode** button in the right sidebar. The button label toggles between "Hunter" and "Activator" to show the current mode. In Activator Mode, the **Re-Spot** panel becomes visible below it.

---

## Posting a Spot (Re-Spotting)

Fill in the Re-Spot panel:

| Field | Description |
|-------|-------------|
| **Park** | Your POTA reference number (e.g., `K-0001`) |
| **Freq** | Your operating frequency in kHz (e.g., `14265`) |
| **Mode** | Your mode (SSB, CW, FT8, FM, etc.) |
| **Comment** | Optional note (e.g., `CQ POTA`, signal report, antenna info) |

Click **Post Spot**. POTA Hunter submits the spot to the POTA API using your callsign as both activator and spotter.

Your spot will appear in the live spot tables for other POTA Hunter users (and on pota.app) within about 15 seconds.

---

## Enabling Re-Spot

If the Re-Spot panel is not visible, go to **Settings** and ensure the **Re-Spot** option is enabled, or toggle the **Activator Mode** button in the sidebar.

---

## Tips for Activators

- **Frequency from Flrig**: If Flrig is connected, the Freq field in the Re-Spot panel can be pre-filled from your current VFO frequency.
- **Re-spot regularly**: The POTA API ages spots out after a while. Post a new spot every 15–20 minutes if you're having a long activation to stay visible to hunters.
- **QRT comment**: When you're done, post one final spot with `QRT` in the comment field. This signals to hunters (and hides the spot in POTA Hunter if "Hide QRT" is checked) that you've left the air.
- **Log your contacts**: Switch back to the log form between re-spotting to log the hunters who called you. You don't need to leave Activator Mode to log QSOs.

---

## What Gets Posted to POTA API

```
Activator: <your callsign>
Spotter:   <your callsign>
Reference: <park number>
Frequency: <freq in kHz>
Mode:      <mode>
Comment:   <your comment>
```

The spot is publicly visible at [pota.app](https://pota.app) and through any POTA-aware application.

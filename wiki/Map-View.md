# Map View

The POTA Hunter map is an interactive world map that shows live spot locations and your logged QSOs at a glance. It opens automatically in your default web browser and is served by a built-in local web server.

---

## Marker Colors

| Color | Meaning |
|-------|---------|
| **Yellow** | Active POTA spot (unworked) |
| **Orange** | Active POTA spot with high activity |
| **Green** | Park you have already logged a QSO with this session |
| **Blue** | Currently selected / tuned spot |

Markers with multiple spots at the same park show a count badge.

---

## Map Controls

| Control | Action |
|---------|--------|
| **Click + drag** | Pan the map |
| **Scroll wheel** | Zoom in / out |
| **+ / − buttons** | Zoom in / out |
| **Click a marker** | Select that spot (pre-fills log form, tunes radio) |
| **Double-click a marker** | Open the Log QSO dialog for that spot |

---

## Toolbar Buttons

| Button | Description |
|--------|-------------|
| **Day / Night** | Toggle between a light daytime map and a dark nighttime map |
| **Radar** | Overlay live weather radar tiles from RainViewer |

---

## Weather Radar

When the radar overlay is enabled, POTA Hunter fetches the latest radar frame URLs from the RainViewer API and composites them onto the map. This is useful for knowing if weather is affecting propagation in a specific region. The radar updates each time the spot list refreshes.

The radar toggle button is also available in the main window header bar.

---

## Real-Time Updates

The map data is served from a local `/data` endpoint that the browser polls on the same interval as the spot scanner. As spots appear, move, or disappear, their markers update automatically. Your logged QSOs are reflected in green immediately after saving.

---

## Park Grid Overlay

For each active spot, a faint grid outline may be shown based on the park's Maidenhead grid square. This helps visualize the general operating area, especially for large national parks that may span multiple grids.

---

## Troubleshooting the Map

**Map doesn't open**: POTA Hunter uses a random available port for the local web server. If your browser blocks localhost connections, allow `127.0.0.1` in your browser's security settings.

**Map is blank**: Make sure the parks database has been downloaded (**File → Update Parks DB**). The map needs lat/lon data from the parks DB to place markers.

**Markers missing for some spots**: Some spots don't include grid or coordinate data. If the park reference is not in the local parks DB, no marker is shown. Updating the parks DB usually fixes this.

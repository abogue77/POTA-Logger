# Interface Overview

The POTA Hunter main window is divided into four main areas.

---

## Header Bar

Runs across the top of the window.

| Element | Description |
|---------|-------------|
| **Status dot** | Green = online (POTA API reachable), Red = offline |
| **UTC Clock** | Live UTC time display |
| **Callsign** | Your configured callsign |
| **Scan button** | Pause / resume automatic spot fetching |
| **Radar button** | Toggle weather radar overlay on the map |
| **Theme button** | Switch between Dark and Light themes |

---

## Left Panel — Log QSO

The QSO entry form. Fields are pre-filled automatically when you click a POTA spot.

| Field | Auto-filled from |
|-------|-----------------|
| **Callsign** | Selected POTA spot |
| **RST Sent** | Defaults to `59` |
| **RST Received** | Defaults to `59` |
| **Park #** | Selected POTA spot |
| **Grid Square** | Parks database lookup by park reference |
| **Comments** | Optional free text |
| **Notes** | Optional free text |
| **Freq / Band / Mode** | Captured from Flrig at the moment **LOG QSO** is clicked |

Below the entry form is a scrollable **QSO table** showing all contacts logged in the current session, with columns: ID, Call, Date (UTC), Time (UTC), Freq, Band, Mode, RST Sent, RST Rcvd, Park, Comment, Notes.

Double-click any row in the QSO table to open the **Edit QSO** dialog.

---

## Center Panel — POTA Map

An interactive world map rendered in your default web browser (served locally by a built-in web server).

See [Map View](Map-View) for full details on controls and marker colors.

---

## Right Panel — POTA Spots

A real-time table of active POTA activations fetched from the POTA API.

**Columns**: Activator, Park, Park Name, Freq, Mode, Spotted (age), Comments

**Color coding**:
- Normal row = unworked activator
- Highlighted row = activator already logged in this session

Above the table are filter controls (band, mode, hide QRT, ITU regions). Below are scan controls.

See [POTA Spots](POTA-Spots) for full details.

---

## Right Sidebar — Status Cards

A narrow strip on the far right showing live status:

| Card | Shows |
|------|-------|
| **POTA Status** | Number of active spots currently displayed |
| **QSOs Logged** | Contact count for the current session |
| **Active Bands** | Chip list of all bands with live activations |
| **Tuned Station** | Callsign, park, freq, and mode of the currently selected spot + **LOG QSO** button |
| **Last Logged** | Summary of the most recently saved QSO |
| **Activator Mode** | Toggle button — switches between Hunter and Activator workflows |
| **Re-Spot** | Park, frequency, mode, and comment inputs + **Post Spot** button (visible in Activator Mode) |

---

## Menu Bar

| Menu | Items |
|------|-------|
| **File** | Open Logbook, Import ADIF, Export ADIF, Update Parks DB, Exit |
| **Settings** | Station Settings, QRZ Login, Flrig Settings |
| **Help** | About |

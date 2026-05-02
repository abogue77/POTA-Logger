# Logging QSOs

---

## Logging from a POTA Spot (Recommended)

1. Find an activation in the **POTA Spots** panel (right side).
2. Click the row — POTA Hunter pre-fills:
   - Callsign, Park #, Grid Square (from the parks database)
   - If Flrig is connected, the radio tunes to the spot's frequency
3. Make the contact.
4. Click **LOG QSO** (in the Tuned Station card or the form's button).
   - Frequency, band, and mode are captured from Flrig at this moment.
   - Date and time are stamped in UTC automatically.
5. The QSO appears instantly in the log table below the entry form.

---

## Logging Manually

Fill in the form fields by hand:

| Field | Notes |
|-------|-------|
| **Callsign** | Required |
| **RST Sent** | Defaults to `59`; change as needed |
| **RST Received** | Defaults to `59`; change as needed |
| **Park #** | POTA reference (e.g., `K-0001`); leave blank if not a POTA QSO |
| **Grid Square** | Auto-populated from the parks DB if Park # is filled |
| **Comments** | Shown in the log table |
| **Notes** | Private notes; also saved to ADIF |

Click **LOG QSO** to save.

---

## Editing a QSO

Double-click any row in the QSO table to open the **Edit QSO** dialog. All fields are editable. Click **Save** to update, or **Cancel** to discard changes.

> Edits trigger a full rewrite of the ADIF file to keep it consistent.

---

## Deleting a QSO

In the **Edit QSO** dialog, click **Delete** and confirm the prompt. The entry is removed from both the in-memory index and the ADIF file.

---

## What Gets Saved to ADIF

Each QSO is stored as an ADIF record with the following fields:

| ADIF Field | Source |
|------------|--------|
| `CALL` | Callsign field |
| `QSO_DATE` | UTC date at log time |
| `TIME_ON` | UTC time at log time |
| `FREQ` | Frequency in MHz (from Flrig or manual) |
| `BAND` | Derived from frequency (e.g., `40m`) |
| `MODE` | Mode (from Flrig or manual) |
| `RST_SENT` | RST Sent field |
| `RST_RCVD` | RST Received field |
| `GRIDSQUARE` | Grid Square field |
| `POTA_REF` | Park # field |
| `COMMENT` | Comments field |
| `NOTES` | Notes field |
| `MY_CALL` | Your callsign from Station Settings |
| `MY_GRIDSQUARE` | Your grid square from Station Settings |

---

## Frequency and Band Mapping

Bands are automatically derived from the logged frequency. POTA Hunter covers 160 m through 70 cm plus satellite:

| Band | Frequency Range |
|------|----------------|
| 160m | 1.8 – 2.0 MHz |
| 80m | 3.5 – 4.0 MHz |
| 60m | 5.3 – 5.4 MHz |
| 40m | 7.0 – 7.3 MHz |
| 30m | 10.1 – 10.15 MHz |
| 20m | 14.0 – 14.35 MHz |
| 17m | 18.068 – 18.168 MHz |
| 15m | 21.0 – 21.45 MHz |
| 12m | 24.89 – 24.99 MHz |
| 10m | 28.0 – 29.7 MHz |
| 6m | 50 – 54 MHz |
| 2m | 144 – 148 MHz |
| 70cm | 420 – 450 MHz |
| SAT | Everything else |

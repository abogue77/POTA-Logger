#!/usr/bin/env python3
"""
POTA Hunter — POTA Activator Hunter & Ham Radio Logger  v2.0
Cross-platform (Windows & Linux).  Standard library only.

Entry form field order:
  Callsign → RST Sent → RST Received → Park # → Comments → Notes
Date/Time : stamped automatically (UTC) when LOG QSO is pressed.
Freq/Band/Mode : pulled live from Flrig on LOG QSO (falls back to VFO bar value).
Storage  : ADIF (.adi) is the primary on-disk format per logbook.
           SQLite is used as a fast in-memory working index, rebuilt on open.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import sqlite3
import xmlrpc.client
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import threading
import datetime
import time
import os
import re
import json
import tempfile
import webbrowser

# ── Paths ─────────────────────────────────────────────────────────────────────
LOGBOOK_DIR = os.path.join(os.path.expanduser("~"), "HamLog")
os.makedirs(LOGBOOK_DIR, exist_ok=True)
CONFIG_FILE   = os.path.join(LOGBOOK_DIR, "config.json")
PARKS_DB      = os.path.join(LOGBOOK_DIR, "pota_parks.db")
PARKS_CSV_URL = "https://pota.app/all_parks_ext.csv"

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "callsign":   "",
    "gridsquare": "",
    "qrz_user":   "",
    "qrz_pass":   "",
    "flrig_host": "127.0.0.1",
    "flrig_port": 12345,
    "last_logbook": "",
    "theme":      "dark",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(json.load(f))
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── SQLite in-memory index ────────────────────────────────────────────────────
def make_index():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE qso (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            call       TEXT NOT NULL,
            date       TEXT NOT NULL,
            time_on    TEXT NOT NULL,
            freq       REAL,
            band       TEXT,
            mode       TEXT,
            rst_sent   TEXT DEFAULT '59',
            rst_rcvd   TEXT DEFAULT '59',
            name       TEXT,
            qth        TEXT,
            gridsquare TEXT,
            park_nr    TEXT,
            comment    TEXT,
            notes      TEXT
        )
    """)
    conn.commit()
    return conn

# ── POTA Parks DB ─────────────────────────────────────────────────────────────
def _latlon_to_grid(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return ""
    lon += 180.0
    lat += 90.0
    field_lon = int(lon / 20)
    field_lat = int(lat / 10)
    sq_lon    = int((lon % 20) / 2)
    sq_lat    = int(lat % 10)
    return (chr(ord('A') + field_lon) +
            chr(ord('A') + field_lat) +
            str(sq_lon) +
            str(sq_lat))

def parks_db_exists():
    if not os.path.exists(PARKS_DB):
        return False
    try:
        with sqlite3.connect(PARKS_DB) as cx:
            n = cx.execute("SELECT COUNT(*) FROM parks").fetchone()[0]
        return n > 0
    except Exception:
        return False

def build_parks_db(progress_cb=None):
    """Download all_parks_ext.csv and rebuild ~/HamLog/pota_parks.db.
    Returns (count, error_string). error_string is None on success.
    Designed to run in a background thread; progress_cb(msg) is thread-safe."""
    import csv, io
    if progress_cb:
        progress_cb("Downloading POTA parks list…")
    try:
        req = urllib.request.Request(
            PARKS_CSV_URL,
            headers={"User-Agent": "POTA-Hunter/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"Download failed: {e}"

    if progress_cb:
        progress_cb("Parsing CSV…")

    reader = csv.DictReader(io.StringIO(raw))
    FIELD_MAP = {
        "reference": ["reference", "park_reference", "park reference"],
        "name":      ["name", "park_name", "park name"],
        "latitude":  ["latitude", "lat"],
        "longitude": ["longitude", "lon", "long"],
        "grid":      ["grid", "gridsquare", "grid_square"],
        "state":     ["locationname", "state", "location_name"],
        "country":   ["entityname", "country", "entity_name"],
    }

    rows = []
    for rec in reader:
        lc = {k.strip().lower(): v.strip() for k, v in rec.items()}

        def pick(candidates):
            for c in candidates:
                if lc.get(c):
                    return lc[c]
            return ""

        ref = pick(FIELD_MAP["reference"]).upper()
        if not ref:
            continue
        lat  = pick(FIELD_MAP["latitude"])
        lon  = pick(FIELD_MAP["longitude"])
        grid = pick(FIELD_MAP["grid"])
        if not grid:
            grid = _latlon_to_grid(lat, lon)
        rows.append((
            ref,
            pick(FIELD_MAP["name"]),
            lat,
            lon,
            grid.upper()[:6],
            pick(FIELD_MAP["state"]),
            pick(FIELD_MAP["country"]),
        ))

    if not rows:
        return 0, "CSV parsed but contained no usable park records."

    if progress_cb:
        progress_cb(f"Writing {len(rows):,} parks to DB…")

    try:
        with sqlite3.connect(PARKS_DB) as cx:
            cx.execute("DROP TABLE IF EXISTS parks")
            cx.execute("""
                CREATE TABLE parks (
                    reference TEXT PRIMARY KEY,
                    name      TEXT,
                    latitude  REAL,
                    longitude REAL,
                    grid      TEXT,
                    state     TEXT,
                    country   TEXT
                )
            """)
            cx.execute("CREATE INDEX IF NOT EXISTS idx_parks_ref ON parks(reference)")
            cx.executemany("INSERT OR REPLACE INTO parks VALUES (?,?,?,?,?,?,?)", rows)
    except Exception as e:
        return 0, f"DB write failed: {e}"

    if progress_cb:
        progress_cb(f"POTA parks DB ready — {len(rows):,} parks.")
    return len(rows), None

def lookup_park(reference):
    """Return a dict with park data or None. Thread-safe (new connection per call)."""
    if not os.path.exists(PARKS_DB):
        return None
    try:
        with sqlite3.connect(PARKS_DB) as cx:
            cx.row_factory = sqlite3.Row
            row = cx.execute(
                "SELECT * FROM parks WHERE reference=?",
                (reference.strip().upper(),)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None

# ── ADIF helpers ──────────────────────────────────────────────────────────────
def adif_field(tag, val):
    if val is None or str(val).strip() == "":
        return ""
    v = str(val).strip()
    return f"<{tag.upper()}:{len(v)}>{v} "

def adif_header(mycall):
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    prog = "POTA Hunter"
    return (f"POTA Hunter ADIF Log — {now}  Station: {mycall}\n"
            + adif_field("ADIF_VER","3.1.0")
            + adif_field("PROGRAMID", prog)
            + "<EOH>\n\n")

def row_to_adif(row, mycall=""):
    r = dict(row)
    date_str = r.get("date","").replace("-","")
    freq_val = r.get("freq")
    freq_str = f"{float(freq_val):.6g}" if freq_val else ""
    parts = (
        adif_field("CALL",             r.get("call","")) +
        adif_field("QSO_DATE",         date_str) +
        adif_field("TIME_ON",          r.get("time_on","")) +
        adif_field("FREQ",             freq_str) +
        adif_field("BAND",             r.get("band","")) +
        adif_field("MODE",             r.get("mode","")) +
        adif_field("RST_SENT",         r.get("rst_sent","")) +
        adif_field("RST_RCVD",         r.get("rst_rcvd","")) +
        adif_field("NAME",             r.get("name","")) +
        adif_field("QTH",              r.get("qth","")) +
        adif_field("GRIDSQUARE",       r.get("gridsquare","")) +
        adif_field("POTA_REF",         r.get("park_nr","")) +
        adif_field("COMMENT",          r.get("comment","")) +
        adif_field("NOTES",            r.get("notes","")) +
        adif_field("STATION_CALLSIGN", mycall)
    )
    return parts.strip() + " <EOR>\n"

def parse_adif_records(text):
    eoh = text.upper().find("<EOH>")
    if eoh >= 0:
        text = text[eoh + 5:]
    records = []
    for rec in re.split(r'<EOR>', text, flags=re.IGNORECASE):
        rec = rec.strip()
        if not rec:
            continue
        d = {}
        for m in re.finditer(r'<(\w+):\d+(?::\w+)?>([^<]*)', rec, re.DOTALL):
            d[m.group(1).upper()] = m.group(2).strip()
        if d.get("CALL","").strip():
            records.append(d)
    return records

def adif_to_row_dict(d):
    qso_date = d.get("QSO_DATE","")
    if len(qso_date) == 8:
        qso_date = f"{qso_date[:4]}-{qso_date[4:6]}-{qso_date[6:]}"
    freq_str = d.get("FREQ","")
    try:
        freq = float(freq_str) if freq_str else None
    except ValueError:
        freq = None
    band = d.get("BAND","") or (freq_to_band(freq) if freq else "")
    return {
        "call":       d.get("CALL","").upper(),
        "date":       qso_date,
        "time_on":    d.get("TIME_ON",""),
        "freq":       freq,
        "band":       band,
        "mode":       d.get("MODE",""),
        "rst_sent":   d.get("RST_SENT","59"),
        "rst_rcvd":   d.get("RST_RCVD","59"),
        "name":       d.get("NAME",""),
        "qth":        d.get("QTH",""),
        "gridsquare": d.get("GRIDSQUARE","") or d.get("GRID",""),
        "park_nr":    d.get("POTA_REF",""),
        "comment":    d.get("COMMENT",""),
        "notes":      d.get("NOTES",""),
    }

def load_adif_into_index(adif_path, conn):
    conn.execute("DELETE FROM qso")
    conn.commit()
    if not os.path.exists(adif_path):
        return 0
    with open(adif_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    records = parse_adif_records(text)
    for rec in records:
        row = adif_to_row_dict(rec)
        conn.execute("""
            INSERT INTO qso (call,date,time_on,freq,band,mode,
                rst_sent,rst_rcvd,name,qth,gridsquare,park_nr,comment,notes)
            VALUES (:call,:date,:time_on,:freq,:band,:mode,
                :rst_sent,:rst_rcvd,:name,:qth,:gridsquare,:park_nr,:comment,:notes)
        """, row)
    conn.commit()
    return len(records)

def rewrite_adif(adif_path, conn, mycall=""):
    rows = conn.execute(
        "SELECT * FROM qso ORDER BY date ASC, time_on ASC").fetchall()
    with open(adif_path, "w", encoding="utf-8") as f:
        f.write(adif_header(mycall))
        for row in rows:
            f.write(row_to_adif(row, mycall))

# ── Frequency ─────────────────────────────────────────────────────────────────
def freq_to_band(freq_mhz):
    try:
        f = float(freq_mhz)
    except (TypeError, ValueError):
        return ""
    bands = [
        (1.8,2.0,"160m"),(3.5,4.0,"80m"),(5.3,5.4,"60m"),
        (7.0,7.3,"40m"),(10.1,10.15,"30m"),(14.0,14.35,"20m"),
        (18.068,18.168,"17m"),(21.0,21.45,"15m"),(24.89,24.99,"12m"),
        (28.0,29.7,"10m"),(50.0,54.0,"6m"),(144.0,148.0,"2m"),
        (420.0,450.0,"70cm"),
    ]
    for lo, hi, band in bands:
        if lo <= f <= hi:
            return band
    return ""

# ── Flrig ─────────────────────────────────────────────────────────────────────
class _TimeoutTransport(xmlrpc.client.Transport):
    """XML-RPC transport with a configurable connection/read timeout."""
    def __init__(self, timeout=2.0):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn

def flrig_get(host, port):
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/RPC2",
            transport=_TimeoutTransport(timeout=2.0),
            allow_none=True)
        return proxy.rig.get_vfo(), proxy.rig.get_mode()
    except Exception:
        return None, None

def flrig_get_all(host, port):
    """Single-session fetch: freq, mode, s-meter, power meter, PTT state."""
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/RPC2",
            transport=_TimeoutTransport(timeout=2.0),
            allow_none=True)
        freq_hz  = proxy.rig.get_vfo()
        mode     = proxy.rig.get_mode()
        smeter   = proxy.rig.get_smeter()
        pwrmeter = proxy.rig.get_pwrmeter()
        try:
            ptt = bool(proxy.rig.get_ptt())
        except Exception:
            ptt = pwrmeter is not None and float(pwrmeter) > 5
        return freq_hz, mode, smeter, pwrmeter, ptt
    except Exception:
        return None, None, None, None, False

def flrig_set_freq(host, port, freq_hz):
    """Returns True on success or an error string on failure."""
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/RPC2",
            transport=_TimeoutTransport(timeout=5.0),
            allow_none=True)
        proxy.rig.set_vfo(float(freq_hz))
        return True
    except Exception as e:
        return str(e)

# ── POTA spot posting ─────────────────────────────────────────────────────────
def pota_post_spot(activator, spotter, reference, freq_khz, mode, comment=""):
    import json as _json
    body = _json.dumps({
        "activator":  activator,
        "spotter":    spotter,
        "reference":  reference,
        "frequency":  str(freq_khz),
        "mode":       mode,
        "comments":   comment or "Spotted via HamLog",
    }).encode()
    req = urllib.request.Request(
        "https://api.pota.app/spot",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "HamLog/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)

# ── QRZ ───────────────────────────────────────────────────────────────────────
_qrz_session = None

def qrz_login(user, password):
    global _qrz_session
    url = (f"https://xmldata.qrz.com/xml/current/?username={urllib.parse.quote(user)}"
           f"&password={urllib.parse.quote(password)}&agent=HamLog2.0")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            tree = ET.parse(r)
        key = tree.find(".//{http://xmldata.qrz.com}Key")
        if key is not None:
            _qrz_session = key.text
            return True
        err = tree.find(".//{http://xmldata.qrz.com}Error")
        return err.text if err is not None else "Login failed"
    except Exception as e:
        return str(e)

def qrz_lookup(call):
    if not _qrz_session:
        return None
    url = (f"https://xmldata.qrz.com/xml/current/?s={_qrz_session}"
           f"&callsign={urllib.parse.quote(call.upper())}")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            tree = ET.parse(r)
        ns = "{http://xmldata.qrz.com}"
        def g(tag):
            el = tree.find(f".//{ns}{tag}")
            return el.text if el is not None else ""
        return {"name": f"{g('fname')} {g('name')}".strip(),
                "qth":  f"{g('addr2')}, {g('country')}".strip(", "),
                "grid": g("grid")}
    except Exception:
        return None

# ── Palettes ──────────────────────────────────────────────────────────────────
DARK_PALETTE = {
    "BG": "#111318", "BG2": "#1a1d24", "BG3": "#22262f", "BG4": "#2a2f3a",
    "ACCENT": "#e8a020", "ACC2": "#4fc3f7", "ACC3": "#81c995",
    "WARN": "#f28b82", "YELLOW": "#d4c020", "MUTED": "#555e6e", "FG": "#dde3ee", "FG2": "#8c95a6",
    "SEL": "#2d3a52",
    "MAP_BG": "#0a0e17", "MAP_GRID": "#151c28", "MAP_GRID2": "#2a3347",
    "MAP_COAST": "#1e3a5f", "MAP_GLOW": "#5a3010",
    "POTA_TUNED": "#0077ff", "POTA_WORKED": "#00cc44",
}
LIGHT_PALETTE = {
    "BG": "#f5f7fa", "BG2": "#eaecf2", "BG3": "#dde1ea", "BG4": "#ced3df",
    "ACCENT": "#b07800", "ACC2": "#0066aa", "ACC3": "#2a7a30",
    "WARN": "#cc2222", "YELLOW": "#b8a000", "MUTED": "#7a8599", "FG": "#1a1d24", "FG2": "#4a5568",
    "SEL": "#b3c9e8",
    "MAP_BG": "#d0dce8", "MAP_GRID": "#b0c4d8", "MAP_GRID2": "#8aaac8",
    "MAP_COAST": "#4a7ab0", "MAP_GLOW": "#d4a040",
    "POTA_TUNED": "#5588ff", "POTA_WORKED": "#00cc55",
}

def _apply_palette(name="dark"):
    g = globals()
    for k, v in (LIGHT_PALETTE if name == "light" else DARK_PALETTE).items():
        g[k] = v

_apply_palette("dark")  # defaults; entry point overrides with saved preference

MONO  = ("Courier New", 10)
DISP  = ("Courier New", 22, "bold")
SM    = ("Courier New", 9)
LBL   = ("Courier New", 10, "bold")
TITLE = ("Courier New", 13, "bold")

MODES = ["USB","LSB","SSB","AM","FM","FMN","CW","CWR","RTTY","RTTYR",
         "PKTUSB","PKTLSB","PKTFM","JS8","FT8","FT4","PSK31","OLIVIA","HELL"]
BANDS = ["160m","80m","60m","40m","30m","20m","17m","15m","12m","10m",
         "6m","2m","70cm","SAT","Other"]

def _make_reticle_img(size, fg_hex, bg_hex):
    """Return a tk.PhotoImage of a scope reticle (crosshair + circle), encoded as PNG."""
    import math, struct, zlib, base64
    def _rgb(h):
        h = h.lstrip('#')
        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
    fg, bg = _rgb(fg_hex), _rgb(bg_hex)
    buf = bytearray()
    for _ in range(size * size):
        buf.extend(bg)
    def put(x, y):
        if 0 <= x < size and 0 <= y < size:
            i = (y * size + x) * 3
            buf[i:i+3] = bytes(fg)
    cx = cy = size // 2
    gap = 3
    radius = size // 2 - 2
    for x in range(size):
        if abs(x - cx) > gap:
            for dy in (-1, 0, 1):
                put(x, cy + dy)
    for y in range(size):
        if abs(y - cy) > gap:
            for dx in (-1, 0, 1):
                put(cx + dx, y)
    for a in range(720):
        rad = math.radians(a / 2)
        put(int(round(cx + radius * math.cos(rad))),
            int(round(cy + radius * math.sin(rad))))
    def _png_chunk(tag, data):
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw.extend(buf[y * size * 3:(y + 1) * size * 3])
    png = (b'\x89PNG\r\n\x1a\n'
           + _png_chunk(b'IHDR', ihdr)
           + _png_chunk(b'IDAT', zlib.compress(bytes(raw)))
           + _png_chunk(b'IEND', b''))
    return tk.PhotoImage(data=base64.b64encode(png).decode())

# ── Simplified world coastline outlines (lon, lat) ────────────────────────────
# Each sub-list is one polyline drawn on the map canvas.
WORLD_OUTLINE = [
    # North America
    [(-168,72),(-152,58),(-130,54),(-125,49),(-124,37),(-117,32),
     (-117,23),(-97,23),(-95,20),(-87,16),(-83,10),(-82,9),(-79,8),
     (-76,10),(-75,11),(-61,11),(-63,18),(-80,25),(-82,29),(-80,32),
     (-75,35),(-76,37),(-75,40),(-74,41),(-70,42),(-70,44),(-67,45),
     (-65,45),(-60,47),(-53,47),(-57,53),(-55,59),(-60,63),(-68,63),
     (-78,63),(-86,65),(-94,65),(-100,66),(-107,70),(-120,70),
     (-140,70),(-165,70),(-168,72)],
    # Greenland
    [(-73,76),(-47,60),(-25,68),(-18,73),(-15,76),(-20,79),
     (-35,81),(-55,83),(-72,82),(-73,76)],
    # South America
    [(-79,8),(-62,11),(-51,4),(-48,-2),(-35,-4),(-35,-9),
     (-40,-20),(-43,-23),(-48,-28),(-53,-34),(-58,-34),(-62,-38),
     (-65,-42),(-65,-55),(-68,-54),(-75,-50),(-72,-40),(-71,-35),
     (-70,-18),(-75,-10),(-80,0),(-79,8)],
    # Europe (main)
    [(-9,36),(-9,39),(-8,44),(-2,43),(3,43),(7,44),(12,44),
     (14,45),(16,41),(18,40),(22,40),(26,38),(28,37),(30,42),
     (36,42),(36,37),(32,36),(28,37),(22,38),(12,38),(8,37),
     (4,38),(0,38),(0,36),(-5,36),(-9,36)],
    # Scandinavia
    [(5,57),(10,55),(15,55),(18,57),(20,60),(22,65),(26,70),
     (28,71),(25,70),(22,68),(18,63),(15,60),(10,55)],
    # Great Britain
    [(-5,50),(0,51),(2,51),(0,52),(-3,56),(-5,58),(-7,57),
     (-8,55),(-6,53),(-5,52),(-5,50)],
    # Ireland
    [(-6,51),(-10,52),(-10,54),(-8,55),(-6,54),(-6,51)],
    # Iceland
    [(-24,64),(-14,64),(-14,66),(-18,67),(-22,66),(-24,64)],
    # Africa
    [(-5,36),(10,37),(25,31),(36,22),(43,12),(50,12),
     (44,10),(40,-10),(36,-18),(34,-26),(32,-30),(28,-34),
     (18,-34),(17,-29),(14,-22),(12,-18),(9,-5),(9,0),(9,4),
     (8,5),(2,5),(-2,5),(-8,5),(-14,8),(-17,14),(-17,21),
     (-13,28),(-8,32),(-5,36)],
    # Madagascar
    [(44,-12),(50,-16),(50,-25),(44,-26),(44,-20),(44,-12)],
    # Arabian Peninsula
    [(36,22),(43,12),(50,12),(58,22),(57,24),(56,26),(55,28),
     (56,30),(44,30),(36,28),(36,22)],
    # Asia: Turkey, Caucasus, Iran, Central Asia north coast (Black/Caspian Sea)
    [(28,41),(30,42),(36,42),(40,42),(44,40),(48,38),(52,38),
     (54,42),(52,46),(54,50),(56,46),(60,44),(62,38),(64,38),
     (68,38),(72,34),(76,34),(80,28),(82,28),(84,28),(86,22),
     (88,22),(92,22),(96,20),(98,18),(100,6),(102,4),(104,2),
     (104,-2),(106,-8),(108,-8),(110,-8),(112,-8),(114,-4),
     (116,0),(118,2),(120,4),(122,4),(126,2),(128,4),
     (130,12),(132,18),(130,24),(126,32),(122,38),(122,40),
     (118,34),(114,22),(108,20),(104,18),(100,14),(96,18),
     (92,22),(88,22),(84,22),(80,26),(80,8),(78,8),(76,8),
     (72,12),(72,22),(68,24),(62,24),(60,22),(56,22),(50,22),
     (48,28),(44,28),(40,36),(36,36),(32,36),(28,37),(28,41)],
    # Russia far east & Siberia north coast
    [(28,68),(40,70),(60,70),(80,72),(100,70),(120,72),(140,72),
     (160,70),(170,64),(170,60),(160,60),(150,50),(142,50),
     (136,44),(130,42),(128,48),(130,52),(132,58),(136,62),
     (140,68),(140,72),(128,72),(120,72)],
    # Kamchatka
    [(160,52),(162,58),(162,60),(158,60),(156,54),(160,52)],
    # Japan Honshu
    [(130,32),(136,35),(140,36),(142,38),(142,42),(140,44),
     (136,44),(134,40),(132,36),(130,32)],
    # Hokkaido
    [(140,42),(142,44),(146,45),(145,43),(142,42)],
    # Korean Peninsula
    [(126,34),(128,36),(130,38),(130,40),(128,40),(126,38),(126,34)],
    # Taiwan
    [(120,22),(122,24),(122,26),(120,26),(120,22)],
    # Sri Lanka
    [(80,6),(82,8),(82,10),(80,10),(80,6)],
    # Australia
    [(114,-22),(114,-26),(116,-34),(118,-34),(122,-34),(126,-34),
     (130,-34),(134,-36),(138,-36),(140,-38),(144,-38),(148,-38),
     (150,-36),(152,-30),(152,-26),(150,-22),(148,-20),(144,-18),
     (140,-16),(136,-14),(130,-14),(126,-14),(122,-18),(118,-20),
     (114,-22)],
    # New Zealand North Island
    [(174,-38),(178,-38),(178,-34),(174,-36),(174,-38)],
    # New Zealand South Island
    [(166,-44),(172,-44),(172,-46),(168,-46),(166,-44)],
    # Papua New Guinea
    [(140,-6),(142,-6),(144,-4),(146,-4),(148,-6),(150,-8),
     (148,-8),(144,-8),(140,-6)],
    # Antarctica (simplified arc)
    [(-180,-70),(-120,-72),(-60,-74),(0,-70),(60,-74),(120,-72),(180,-70)],
]

# ── Maidenhead grid decoder ───────────────────────────────────────────────────
def grid_to_latlon(gs):
    """Return (lat, lon) center of a 4-char Maidenhead grid square."""
    gs = (gs or "").strip().upper()
    if len(gs) < 4:
        return None, None
    try:
        lon = (ord(gs[0]) - ord('A')) * 20 - 180 + int(gs[2]) * 2 + 1
        lat = (ord(gs[1]) - ord('A')) * 10 - 90  + int(gs[3]) * 1 + 0.5
        return lat, lon
    except (ValueError, IndexError):
        return None, None

# ══════════════════════════════════════════════════════════════════════════════
class POTAHunter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("POTA Hunter by n5eab")
        self.configure(bg=BG)
        self.minsize(1000, 640)
        self.resizable(True, True)

        self.cfg           = load_config()
        self.conn          = make_index()
        self.adif_path     = ""
        self._map_poll_id    = None
        self._map_adif_mtime = None
        self._flrig_freq_hz  = None
        self._flrig_mode     = None
        self._flrig_poll_id  = None
        self._meter_value    = 0
        self._meter_is_tx    = False
        self._tune_suppress_until = 0.0
        self._pota_paused    = False
        self._pota_loaded    = False
        self._pota_after_id  = None
        self._pota_spots_raw = []
        self._freq_check_var    = tk.StringVar()
        self._freq_check_border = None
        self._pota_band_var  = tk.StringVar(value="All")
        self._pota_mode_var  = tk.StringVar(value="All")
        self._pota_hide_qrt  = tk.BooleanVar(value=False)
        self._pota_clicked_hz    = None
        self._pota_scan_active       = False
        self._pota_scan_idx          = 0
        self._pota_scan_after_id     = None
        self._pota_scan_interval     = tk.IntVar(value=15)
        self._pota_scan_skip_worked  = tk.BooleanVar(value=False)
        self._pota_spot_ctx          = None
        self._pota_respot_enabled    = tk.BooleanVar(value=False)
        self._map_markers   = {}
        self._map_drawn     = False
        self._map_resize_id = None

        self._style_ttk()
        self._build_menu()
        self._build_ui()

        last = self.cfg.get("last_logbook","")
        if last and os.path.exists(last):
            self._open_adif(last)
        else:
            self.after(200, self._prompt_logbook)

        if self.cfg["qrz_user"] and self.cfg["qrz_pass"]:
            threading.Thread(target=self._qrz_login_bg, daemon=True).start()

        self._start_flrig_poll()
        self.after(1500, self._check_parks_db_on_startup)

    # ── TTK style ─────────────────────────────────────────────────────────
    def _style_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview", background=BG2, foreground=FG,
                    fieldbackground=BG2, rowheight=22, font=MONO)
        s.configure("Treeview.Heading", background=BG3, foreground=ACCENT,
                    font=LBL, relief="flat")
        # The clam theme (and Windows visual styles) inject a
        # ("!disabled", "!selected") map entry that overrides tag backgrounds
        # for all normal rows.  Strip it so tag_configure colours show through.
        def _strip(opt):
            return [e for e in s.map("Treeview", query_opt=opt)
                    if e[:2] != ("!disabled", "!selected")]
        s.map("Treeview",
              background=_strip("background") + [("selected", SEL)],
              foreground=_strip("foreground") + [("selected", ACC2)])
        s.configure("TCombobox", fieldbackground=BG3, background=BG3,
                    foreground=FG, arrowcolor=ACCENT)
        s.map("TCombobox", fieldbackground=[("readonly",BG3)],
                           foreground=[("readonly",FG)])
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=BG3, foreground=FG2,
                    font=LBL, padding=[14, 5])
        s.map("TNotebook.Tab",
              background=[("selected", BG2)],
              foreground=[("selected", ACCENT)])

    # ── Menu ──────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb = tk.Menu(self, bg=BG2, fg=FG, activebackground=BG4,
                     activeforeground=ACCENT, relief="flat", bd=0)
        self.config(menu=mb)
        def menu(label):
            m = tk.Menu(mb, tearoff=0, bg=BG2, fg=FG,
                        activebackground=BG4, activeforeground=ACCENT)
            mb.add_cascade(label=label, menu=m)
            return m
        fm = menu("File")
        fm.add_command(label="New Logbook…",  command=self._new_logbook)
        fm.add_command(label="Open Logbook…", command=self._choose_logbook)
        fm.add_separator()
        fm.add_command(label="Import ADIF…",  command=self._import_adif)
        fm.add_command(label="Export ADIF…",  command=self._export_adif)
        fm.add_separator()
        fm.add_command(label="Exit",          command=self.destroy)
        sm = menu("Settings")
        sm.add_command(label="Station Settings…", command=self._station_settings)
        sm.add_command(label="QRZ Login…",        command=self._qrz_settings)
        sm.add_command(label="Flrig Settings…",   command=self._flrig_settings)
        sm.add_separator()
        sm.add_command(label="Update POTA Parks DB…", command=self._update_parks_db)
        sm.add_separator()
        theme_label = ("☀ Switch to Light Mode"
                       if self.cfg.get("theme", "dark") == "dark"
                       else "☾ Switch to Dark Mode")
        sm.add_command(label=theme_label, command=self._switch_theme)
        hm = menu("Help")
        hm.add_command(label="About", command=self._about)

    # ── Main UI ───────────────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG, pady=5)
        top.pack(fill="x", padx=14)
        tk.Label(top, text="◈ POTA Hunter by n5eab", bg=BG, fg=ACCENT, font=TITLE).pack(side="left")
        self._logbook_lbl = tk.Label(top, text="No logbook open", bg=BG, fg=FG2, font=SM)
        self._logbook_lbl.pack(side="left", padx=14)
        self._flrig_lbl = tk.Label(top, text="● Flrig: offline", bg=BG, fg=WARN, font=SM)
        self._flrig_lbl.pack(side="right", padx=6)
        self._qrz_lbl = tk.Label(top, text="QRZ: —", bg=BG, fg=FG2, font=SM)
        self._qrz_lbl.pack(side="right", padx=6)
        self._mycall_lbl = tk.Label(top, text="", bg=BG, fg=ACC2, font=LBL)
        self._mycall_lbl.pack(side="right", padx=6)
        self._update_mycall_lbl()

        # VFO live bar
        vfo_bar = tk.Frame(self, bg=BG2)
        vfo_bar.pack(fill="x", padx=14, pady=(0,6))
        vi = tk.Frame(vfo_bar, bg=BG2)
        vi.pack(padx=12, pady=6, anchor="w")
        tk.Label(vi, text="RIG VFO", bg=BG2, fg=MUTED, font=SM).grid(row=0,column=0)
        self._vfo_freq = tk.Label(vi, text="—", bg=BG2, fg=ACCENT, font=DISP,
                                  width=14, anchor="w")
        self._vfo_freq.grid(row=0,column=1,padx=(8,20))
        tk.Label(vi, text="MODE", bg=BG2, fg=MUTED, font=SM).grid(row=0,column=2)
        self._vfo_mode = tk.Label(vi, text="—", bg=BG2, fg=ACC2,
                                  font=("Courier New",18,"bold"), width=8, anchor="w")
        self._vfo_mode.grid(row=0,column=3,padx=(6,20))
        tk.Label(vi, text="BAND", bg=BG2, fg=MUTED, font=SM).grid(row=0,column=4)
        self._vfo_band = tk.Label(vi, text="—", bg=BG2, fg=ACC3,
                                  font=("Courier New",14,"bold"), width=5, anchor="w")
        self._vfo_band.grid(row=0,column=5,padx=(6,20))
        tk.Label(vi, text="← captured automatically on LOG QSO",
                 bg=BG2, fg=MUTED, font=SM).grid(row=0,column=6,padx=4)

        # S-meter / power-meter bar
        meter_row = tk.Frame(vfo_bar, bg=BG2)
        meter_row.pack(fill="x", padx=12, pady=(0,6))
        self._meter_type_lbl = tk.Label(meter_row, text="S-METER", width=8,
                                        anchor="w", bg=BG2, fg=MUTED, font=SM)
        self._meter_type_lbl.pack(side="left")
        self._meter_canvas = tk.Canvas(meter_row, height=14, bg=BG3,
                                       bd=0, highlightthickness=0)
        self._meter_canvas.pack(side="left", fill="x", expand=True, padx=(4,4))
        self._meter_val_lbl = tk.Label(meter_row, text="  0%", width=5,
                                       anchor="e", bg=BG2, fg=MUTED, font=SM)
        self._meter_val_lbl.pack(side="left")
        self._meter_canvas.bind("<Configure>",
                                lambda e: self._draw_meter_bar(self._meter_value,
                                                               self._meter_is_tx))

        # Entry form
        form_frame = tk.LabelFrame(self, text=" NEW QSO ", bg=BG, fg=ACCENT,
                                   font=LBL, bd=1, relief="groove")
        form_frame.pack(fill="x", padx=14, pady=(0,6))
        self._build_entry_form(form_frame)

        # ── Notebook (3 tabs) ──────────────────────────────────────────────
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=14, pady=(0,4))

        tab1 = tk.Frame(self._nb, bg=BG)
        tab2 = tk.Frame(self._nb, bg=BG)
        tab3 = tk.Frame(self._nb, bg=MAP_BG)

        self._nb.add(tab3, text="  POTA Spots  ")
        self._nb.add(tab1, text="  QSO Log  ")
        self._nb.add(tab2, text="  Grid Map  ")

        self._build_tab_log(tab1)
        self._build_tab_map(tab2)
        self._build_tab_pota(tab3)

        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Bottom status
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0,4))
        self._status_var = tk.StringVar(value="Ready.")
        tk.Label(bot, textvariable=self._status_var, bg=BG, fg=FG2,
                 font=SM, anchor="w").pack(side="left")
        bc = dict(font=SM, relief="flat", cursor="hand2", pady=3, padx=10)
        tk.Button(bot, text="✎ Edit",   bg=BG3, fg=FG,
                  command=self._edit_qso,   **bc).pack(side="right")
        tk.Button(bot, text="✕ Delete", bg=WARN, fg=BG,
                  command=self._delete_qso, **bc).pack(side="right", padx=4)

    # ── Tab 1: QSO Log ────────────────────────────────────────────────────
    def _build_tab_log(self, parent):
        # Search bar
        srch = tk.Frame(parent, bg=BG)
        srch.pack(fill="x", padx=4, pady=(4,4))
        tk.Label(srch, text="SEARCH:", bg=BG, fg=FG2, font=LBL).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_after_id = None
        def _debounced_filter(*_):
            if self._search_after_id:
                self.after_cancel(self._search_after_id)
            self._search_after_id = self.after(150, self._apply_filter)
        self._search_var.trace_add("write", _debounced_filter)
        tk.Entry(srch, textvariable=self._search_var, bg=BG3, fg=FG,
                 font=MONO, relief="flat", insertbackground=ACCENT, bd=4,
                 width=26).pack(side="left", padx=4)
        tk.Label(srch, text="BAND:", bg=BG, fg=FG2, font=LBL).pack(side="left", padx=(10,0))
        self._filter_band = ttk.Combobox(srch, values=["All"]+BANDS,
                                         width=7, font=SM, state="readonly")
        self._filter_band.set("All")
        self._filter_band.bind("<<ComboboxSelected>>", lambda _: self._apply_filter())
        self._filter_band.pack(side="left", padx=4)
        tk.Label(srch, text="MODE:", bg=BG, fg=FG2, font=LBL).pack(side="left", padx=(8,0))
        self._filter_mode = ttk.Combobox(srch, values=["All"]+MODES,
                                         width=8, font=SM, state="readonly")
        self._filter_mode.set("All")
        self._filter_mode.bind("<<ComboboxSelected>>", lambda _: self._apply_filter())
        self._filter_mode.pack(side="left", padx=4)
        tk.Button(srch, text="✕ Clear", bg=BG3, fg=FG2, font=SM,
                  relief="flat", cursor="hand2", padx=6,
                  command=self._clear_filter).pack(side="left", padx=6)
        self._qso_count_lbl = tk.Label(srch, text="0 QSOs", bg=BG, fg=FG2, font=SM)
        self._qso_count_lbl.pack(side="right")

        # Logbook table
        tbl = tk.Frame(parent, bg=BG)
        tbl.pack(fill="both", expand=True, padx=4, pady=(0,4))
        cols = ("id","Call","Date","Time UTC","Freq MHz","Band","Mode",
                "RST Snt","RST Rcv","Park #","Comments","Notes")
        self._tree = ttk.Treeview(tbl, columns=cols, show="headings",
                                  height=14, selectmode="browse")
        widths = [34,90,88,68,92,55,65,62,62,80,160,160]
        for col,w in zip(cols,widths):
            self._tree.heading(col, text=col,
                               command=lambda c=col: self._sort_by(c))
            self._tree.column(col, width=w, anchor="center", minwidth=28)
        vsb = tk.Scrollbar(tbl, orient="vertical",   command=self._tree.yview, bg=BG3)
        hsb = tk.Scrollbar(tbl, orient="horizontal", command=self._tree.xview, bg=BG3)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0,column=0,sticky="nsew")
        vsb.grid(row=0,column=1,sticky="ns")
        hsb.grid(row=1,column=0,sticky="ew")
        tbl.rowconfigure(0,weight=1); tbl.columnconfigure(0,weight=1)
        self._tree.tag_configure("odd",  background=BG2, foreground=FG)
        self._tree.tag_configure("even", background=BG3, foreground=FG)
        self._tree.bind("<<TreeviewSelect>>", self._on_qso_select)
        self._tree.bind("<Double-1>",         self._edit_qso)
        self._tree.bind("<Delete>",    lambda _: self._delete_qso())

    # ── Tab 2: Grid Map ───────────────────────────────────────────────────
    def _build_tab_map(self, parent):
        # Toolbar
        tb = tk.Frame(parent, bg=MAP_BG)
        tb.pack(fill="x", padx=6, pady=(4,2))
        tk.Label(tb, text="GRID MAP", bg=MAP_BG, fg=ACCENT, font=LBL).pack(side="left")
        self._map_count_lbl = tk.Label(tb, text="", bg=MAP_BG, fg=FG2, font=SM)
        self._map_count_lbl.pack(side="left", padx=12)
        tk.Button(tb, text="⟳ Refresh", bg=BG3, fg=FG, font=SM,
                  relief="flat", cursor="hand2", padx=8,
                  command=self._refresh_map).pack(side="right")
        tk.Button(tb, text="Open in Browser", bg=BG3, fg=FG, font=SM,
                  relief="flat", cursor="hand2", padx=8,
                  command=self._open_leaflet_map).pack(side="right", padx=6)
        tk.Label(tb, text="Hover over a marker to see callsigns",
                 bg=MAP_BG, fg=MUTED, font=SM).pack(side="right", padx=10)

        # Canvas
        self._map_canvas = tk.Canvas(parent, bg=MAP_BG,
                                     highlightthickness=0, bd=0)
        self._map_canvas.pack(fill="both", expand=True, padx=6, pady=(0,6))

        # Tooltip label (hidden until hover)
        self._map_tooltip = tk.Label(self._map_canvas, text="", bg=BG4, fg=FG,
                                     font=SM, relief="flat", bd=0,
                                     padx=6, pady=3)

        self._map_canvas.bind("<Configure>", self._on_map_resize)
        self._map_canvas.bind("<Motion>",    self._on_map_motion)
        self._map_canvas.bind("<Leave>",     lambda _: self._map_tooltip.place_forget())

    def _on_map_resize(self, _=None):
        if self._map_resize_id:
            self.after_cancel(self._map_resize_id)
        self._map_resize_id = self.after(120, self._full_map_redraw)

    def _full_map_redraw(self):
        canvas = self._map_canvas
        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 10 or H < 10:
            return
        canvas.delete("all")

        def px(lon, lat):
            x = (lon + 180) / 360 * W
            y = (90  - lat) / 180 * H
            return x, y

        # Faint Maidenhead grid lines
        for i in range(19):
            lon = -180 + i * 20
            x, _ = px(lon, 0)
            canvas.create_line(x, 0, x, H, fill=MAP_GRID, width=1, tags="static")
        for j in range(19):
            lat = 90 - j * 10
            _, y = px(0, lat)
            canvas.create_line(0, y, W, y, fill=MAP_GRID, width=1, tags="static")

        # Field column labels (A–R) along top
        for i in range(18):
            x, _ = px(-180 + i * 20 + 10, 0)
            canvas.create_text(x, 10, text=chr(ord('A') + i),
                               fill=MAP_GRID2, font=("Courier New", 7), tags="static")
        # Field row labels (R–A top to bottom) along left
        for j in range(18):
            _, y = px(0, 90 - j * 10 - 5)
            canvas.create_text(8, y, text=chr(ord('A') + 17 - j),
                               fill=MAP_GRID2, font=("Courier New", 7), tags="static")

        # World outline
        for poly in WORLD_OUTLINE:
            if len(poly) < 2:
                continue
            pts = []
            for lon, lat in poly:
                x, y = px(lon, lat)
                pts.extend((x, y))
            if len(pts) >= 4:
                canvas.create_line(pts, fill=MAP_COAST, width=1, tags="static")

        self._map_drawn = True
        self._draw_map_markers(W, H)

    def _refresh_map(self):
        if not hasattr(self, '_map_canvas'):
            return
        canvas = self._map_canvas
        self.update_idletasks()
        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 10 or H < 10:
            return
        if not self._map_drawn:
            self._full_map_redraw()
            return
        self._draw_map_markers(W, H)

    def _read_adif_grids(self):
        """Read the active ADIF log file and return grouped grid square data."""
        if not self.adif_path or not os.path.exists(self.adif_path):
            return []
        try:
            with open(self.adif_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return []
        records = parse_adif_records(text)
        groups = {}
        for rec in records:
            raw_gs = (rec.get("GRIDSQUARE", "") or rec.get("GRID", "")).strip().upper()[:4]
            if len(raw_gs) < 4:
                continue
            call = rec.get("CALL", "").strip()
            if raw_gs not in groups:
                groups[raw_gs] = {"calls": [], "cnt": 0}
            groups[raw_gs]["cnt"] += 1
            if call and call not in groups[raw_gs]["calls"]:
                groups[raw_gs]["calls"].append(call)
        return [{"gs": gs, "calls": ", ".join(v["calls"]), "cnt": v["cnt"]}
                for gs, v in groups.items()]

    def _draw_map_markers(self, W, H):
        canvas = self._map_canvas
        canvas.delete("marker")
        self._map_markers = {}

        rows = self._read_adif_grids()

        for row in rows:
            gs = row["gs"]
            lat, lon = grid_to_latlon(gs)
            if lat is None:
                continue
            x = (lon + 180) / 360 * W
            y = (90 - lat) / 180 * H
            cnt = row["cnt"]
            canvas.create_rectangle(x-7, y-5, x+7, y+5,
                                    fill=MAP_GLOW, outline="", tags="marker")
            canvas.create_rectangle(x-5, y-3, x+5, y+3,
                                    fill=ACCENT, outline="", tags="marker")
            if cnt > 1:
                canvas.create_text(x, y, text=str(cnt),
                                   fill=BG, font=("Courier New", 7, "bold"),
                                   tags="marker")
            self._map_markers[(round(x), round(y))] = (
                f"{row['calls']}  [{gs}]  ×{cnt}"
            )

        n = len(rows)
        total_qsos = sum(r["cnt"] for r in rows)
        self._map_count_lbl.config(
            text=f"{n} grid square{'s' if n != 1 else ''} ({total_qsos} QSO{'s' if total_qsos != 1 else ''})")

    def _on_map_motion(self, event):
        canvas = self._map_canvas
        closest_label = None
        min_dist = 18
        for (cx, cy), label in self._map_markers.items():
            d = ((event.x - cx) ** 2 + (event.y - cy) ** 2) ** 0.5
            if d < min_dist:
                min_dist = d
                closest_label = label
        if closest_label:
            self._map_tooltip.config(text=closest_label)
            tx = min(event.x + 12, canvas.winfo_width() - 220)
            ty = max(event.y - 22, 4)
            self._map_tooltip.place(x=tx, y=ty)
        else:
            self._map_tooltip.place_forget()

    def _open_leaflet_map(self):
        rows = self._read_adif_grids()

        markers_js = []
        for row in rows:
            gs = row["gs"]
            lat, lon = grid_to_latlon(gs)
            if lat is None:
                continue
            popup = json.dumps(f"{row['calls']}  [{gs}]  ×{row['cnt']}")
            markers_js.append(
                f'L.circleMarker([{lat},{lon}],{{radius:7,color:"#e8a020",'
                f'fillColor:"#e8a020",fillOpacity:0.85}}).bindPopup({popup}).addTo(map);'
            )

        html = (
            '<!DOCTYPE html>\n'
            '<html><head><meta charset="utf-8"><title>POTA Hunter Grid Map</title>\n'
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n'
            '<style>html,body,#map{height:100%;margin:0;background:#111318;}</style>\n'
            '</head><body><div id="map"></div><script>\n'
            'var map=L.map("map",{center:[20,0],zoom:2});\n'
            'L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",{\n'
            '  attribution:"&copy; OpenStreetMap contributors &copy; CARTO",\n'
            '  subdomains:"abcd",maxZoom:19}).addTo(map);\n'
            + "\n".join(markers_js) + "\n"
            '</script></body></html>'
        )

        tmp = os.path.join(tempfile.gettempdir(), "hamlog_map.html")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        webbrowser.open(tmp)

    def _start_map_poll(self):
        self._stop_map_poll()
        self._do_map_poll()

    def _do_map_poll(self):
        try:
            mtime = os.path.getmtime(self.adif_path) if self.adif_path and os.path.exists(self.adif_path) else None
        except OSError:
            mtime = None
        if mtime != self._map_adif_mtime:
            self._map_adif_mtime = mtime
            self._refresh_map()
        self._map_poll_id = self.after(5000, self._do_map_poll)

    def _stop_map_poll(self):
        if self._map_poll_id:
            self.after_cancel(self._map_poll_id)
            self._map_poll_id = None

    # ── Tab 3: POTA Spots ─────────────────────────────────────────────────
    def _build_tab_pota(self, parent):
        PBGK = MAP_BG

        # Toolbar
        tb = tk.Frame(parent, bg=PBGK)
        tb.pack(fill="x", padx=6, pady=(4,2))
        tk.Label(tb, text="POTA ACTIVATORS", bg=PBGK, fg=ACCENT, font=LBL).pack(side="left")
        self._pota_status_lbl = tk.Label(tb, text="Not loaded", bg=PBGK, fg=FG2, font=SM,
                                         width=38, anchor="w")
        self._pota_status_lbl.pack(side="left", padx=12)
        tk.Label(tb, text="Band:", bg=PBGK, fg=FG2, font=SM).pack(side="left", padx=(0, 2))
        self._pota_band_cb = ttk.Combobox(
            tb, textvariable=self._pota_band_var,
            values=["All"], width=6, state="readonly", font=SM)
        self._pota_band_cb.pack(side="left")
        self._pota_band_cb.bind("<<ComboboxSelected>>", lambda _: self._apply_pota_filters())
        tk.Label(tb, text="Mode:", bg=PBGK, fg=FG2, font=SM).pack(side="left", padx=(8, 2))
        self._pota_mode_cb = ttk.Combobox(
            tb, textvariable=self._pota_mode_var,
            values=["All"], width=7, state="readonly", font=SM)
        self._pota_mode_cb.pack(side="left")
        self._pota_mode_cb.bind("<<ComboboxSelected>>", lambda _: self._apply_pota_filters())
        ttk.Checkbutton(
            tb, text="Hide QRT", variable=self._pota_hide_qrt,
            command=self._apply_pota_filters).pack(side="left", padx=(10, 0))
        self._pota_pause_btn = tk.Button(
            tb, text="⏸ Pause Updates", bg=BG3, fg=FG, font=SM,
            relief="flat", cursor="hand2", padx=8, width=16,
            command=self._toggle_pota_pause)
        self._pota_pause_btn.pack(side="right")
        tk.Button(tb, text="⟳ Refresh", bg=BG3, fg=FG, font=SM,
                  relief="flat", cursor="hand2", padx=8,
                  command=self._manual_pota_refresh).pack(side="right", padx=6)
        self._pota_scan_btn = tk.Button(
            tb, text="▶ Scan", bg=BG3, fg=FG, font=SM,
            relief="flat", cursor="hand2", padx=8, width=12,
            command=self._toggle_pota_scan)
        self._pota_scan_btn.pack(side="right", padx=6)
        ttk.Checkbutton(
            tb, text="Skip worked", variable=self._pota_scan_skip_worked
        ).pack(side="right", padx=(0, 6))
        ttk.Checkbutton(
            tb, text="Auto re-spot QSO", variable=self._pota_respot_enabled
        ).pack(side="right", padx=(0, 6))
        tk.Label(tb, text="s", bg=PBGK, fg=FG2, font=SM).pack(side="right")
        tk.Spinbox(tb, from_=5, to=60, increment=5,
                   textvariable=self._pota_scan_interval,
                   width=4, bg=BG3, fg=FG, font=SM,
                   relief="flat", justify="center",
                   buttonbackground=BG3).pack(side="right")
        tk.Label(tb, text="Interval:", bg=PBGK, fg=FG2, font=SM).pack(side="right", padx=(8, 2))

        # Treeview
        frm = tk.Frame(parent, bg=PBGK)
        frm.pack(fill="both", expand=True, padx=6, pady=(2,6))

        pcols = ("Activator","Park","Park Name","Freq","Mode","Spotted","Comments")
        self._pota_tree = ttk.Treeview(frm, columns=pcols, show="headings",
                                       height=14, selectmode="browse")
        pwidths = [90, 80, 200, 85, 65, 75, 250]
        for col, w in zip(pcols, pwidths):
            self._pota_tree.heading(col, text=col)
            self._pota_tree.column(col, width=w, anchor="center", minwidth=40)
        self._pota_tree.column("Park Name", anchor="w")
        self._pota_tree.column("Comments",  anchor="w")

        pvsb = tk.Scrollbar(frm, orient="vertical",   command=self._pota_tree.yview, bg=BG3)
        phsb = tk.Scrollbar(frm, orient="horizontal", command=self._pota_tree.xview, bg=BG3)
        self._pota_tree.configure(yscrollcommand=pvsb.set, xscrollcommand=phsb.set)
        self._pota_tree.grid(row=0, column=0, sticky="nsew")
        pvsb.grid(row=0, column=1, sticky="ns")
        phsb.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        self._pota_tree.tag_configure("odd",    background=BG2, foreground=FG)
        self._pota_tree.tag_configure("even",   background=BG3, foreground=FG)
        self._pota_tree.tag_configure("tuned",  background=POTA_TUNED,  foreground="#000000")
        self._pota_tree.tag_configure("worked", background=POTA_WORKED, foreground="#000000", font=("Courier New", 10, "bold"))
        self._pota_tree.bind("<<TreeviewSelect>>", self._on_pota_spot_select)
        self._pota_tree.bind("<Button-1>", self._on_pota_tree_click)

    def _on_tab_changed(self, _=None):
        try:
            tab = self._nb.tab(self._nb.select(), "text").strip()
        except Exception:
            return
        if tab == "Grid Map":
            self._start_map_poll()
        else:
            self._stop_map_poll()
        if tab == "POTA Spots" and not self._pota_loaded:
            self._pota_loaded = True
            threading.Thread(target=self._fetch_pota_spots, daemon=True).start()

    def _fetch_pota_spots(self):
        try:
            req = urllib.request.Request(
                "https://api.pota.app/spot/activator",
                headers={"User-Agent": "HamLog/2.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                spots = json.loads(r.read().decode("utf-8"))
            spots.sort(
                key=lambda s: str(s.get("spotTime", s.get("timestamp", ""))),
                reverse=True)
            self._pota_spots_raw = spots
            self.after(0, self._apply_pota_filters)
        except Exception as e:
            self.after(0, self._pota_status_lbl.config,
                       {"text": f"⚠ Fetch failed: {e}", "fg": WARN})
        finally:
            if not self._pota_paused:
                self._pota_after_id = self.after(60_000, self._auto_refresh_pota)

    def _apply_pota_filters(self, *_):
        spots = self._pota_spots_raw

        band_sel = self._pota_band_var.get()
        if band_sel and band_sel != "All":
            def _mhz(s):
                try:
                    return float(s.get("frequency", s.get("freq", 0)) or 0) / 1000
                except Exception:
                    return 0.0
            spots = [s for s in spots if freq_to_band(_mhz(s)) == band_sel]

        mode_sel = self._pota_mode_var.get()
        if mode_sel and mode_sel != "All":
            spots = [s for s in spots
                     if str(s.get("mode", "")).upper() == mode_sel.upper()]

        if self._pota_hide_qrt.get():
            spots = [s for s in spots
                     if "qrt" not in str(
                         s.get("comments", s.get("comment", ""))).lower()]

        all_bands = sorted({
            freq_to_band(float(s.get("frequency", s.get("freq", 0)) or 0) / 1000)
            for s in self._pota_spots_raw
        } - {""})
        self._pota_band_cb["values"] = ["All"] + all_bands

        all_modes = sorted({
            str(s.get("mode", "")).upper()
            for s in self._pota_spots_raw
            if s.get("mode", "")
        })
        self._pota_mode_cb["values"] = ["All"] + all_modes

        self._populate_pota_table(spots)

    def _on_pota_tree_click(self, event=None):
        if self._pota_scan_active:
            self._stop_pota_scan()

    def _on_pota_spot_select(self, event=None):
        sel = self._pota_tree.selection()
        if not sel:
            return
        # columns: Activator | Park | Park Name | Freq | Mode | Spotted | Comments
        values = self._pota_tree.item(sel[0], "values")
        activator = values[0]
        park      = values[1]
        park_name = values[2]  # Park name from POTA API
        freq_str  = values[3]
        self._pota_spot_ctx = {
            "activator": activator,
            "reference": park,
            "freq_khz":  freq_str,
            "mode":      values[4],
        }

        # Populate QSO entry fields
        self.e_call.delete(0, "end")
        self.e_call.insert(0, activator.upper())
        self.e_park.delete(0, "end")
        self.e_park.insert(0, park)

        # Look up park in database and populate grid square
        park_data = lookup_park(park)
        if park_data and park_data.get("grid"):
            self.e_grid.delete(0, "end")
            self.e_grid.insert(0, park_data["grid"][:4])  # Maidenhead grid square (4 characters)
        
        # Update park info label with park name from spot data
        if park_name:
            self._park_info_lbl.config(text=f"{park_name} ({park})", fg=ACC3)
        else:
            self._park_info_lbl.config(text=f"Park {park}", fg=MUTED)

        # Tune radio — POTA API returns frequency in kHz (e.g. 14225 = 14.225 MHz)
        try:
            freq_khz = float(freq_str)
            freq_hz = int(freq_khz * 1_000)
            freq_mhz_disp = f"{freq_khz / 1000:.4f}"
        except (ValueError, TypeError):
            return

        # Immediately highlight this row and suppress the flrig poll
        # from overwriting it before the tune command completes.
        self._pota_clicked_hz = freq_hz
        self._tune_suppress_until = time.monotonic() + 4.0
        self._refresh_pota_highlights()
        self._pota_tree.selection_set([])
        host = self.cfg["flrig_host"]
        port = self.cfg["flrig_port"]
        self._pota_status_lbl.config(text=f"Tuning to {freq_mhz_disp} MHz…", fg=FG2)

        def _tune():
            result = flrig_set_freq(host, port, freq_hz)
            if result is True:
                msg = f"Tuned → {freq_mhz_disp} MHz"
                fg  = ACC3
                self._tune_suppress_until = time.monotonic() + 3.0
                self.after(0, lambda: self._update_vfo_display(freq_hz, self._flrig_mode, force=True))
            else:
                msg = f"Tune failed: {result}"
                fg  = WARN
            self.after(0, lambda: self._pota_status_lbl.config(text=msg, fg=fg))

        threading.Thread(target=_tune, daemon=True).start()

    def _populate_pota_table(self, spots):
        self._pota_tree.delete(*self._pota_tree.get_children())
        for s in spots:
            act   = s.get("activator",  s.get("activatorCallsign", ""))
            park  = s.get("reference",  s.get("parkReference", ""))
            pname = s.get("name",       s.get("parkName", ""))
            freq  = s.get("frequency",  s.get("freq", ""))
            mode  = s.get("mode", "")
            stime = s.get("spotTime",   s.get("timestamp", ""))
            if stime and "T" in str(stime):
                stime = str(stime).split("T")[1][:5] + "z"
            cmts  = s.get("comments",   s.get("comment", ""))
            self._pota_tree.insert("", "end",
                values=(act, park, pname, freq, mode, stime, cmts))
        self._refresh_pota_highlights()
        self._check_freq_conflict()
        now = datetime.datetime.utcnow().strftime("%H:%M:%Sz")
        self._pota_status_lbl.config(
            text=f"● {len(spots)} activators  last updated {now}", fg=ACC3)

    def _refresh_pota_highlights(self):
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT UPPER(TRIM(call)) FROM qso").fetchall()
            worked = {r[0] for r in rows if r[0]}
        except Exception:
            worked = set()
        # During the suppress window (user just clicked a spot) always use the
        # clicked frequency so the flrig poll can't overwrite the highlight
        # before the tune command finishes.  Outside that window, prefer the
        # live VFO; fall back to the last-clicked spot when flrig is offline.
        if time.monotonic() < self._tune_suppress_until and self._pota_clicked_hz:
            vfo_hz = self._pota_clicked_hz
        else:
            vfo_hz = self._flrig_freq_hz if self._flrig_freq_hz is not None else self._pota_clicked_hz
        for i, iid in enumerate(self._pota_tree.get_children()):
            vals = self._pota_tree.item(iid, "values")
            activator = str(vals[0]).strip().upper()
            base = "even" if i % 2 == 0 else "odd"
            if activator in worked:
                self._pota_tree.item(iid, tags=("worked",))
                continue
            if vfo_hz is not None:
                try:
                    spot_hz = int(float(vals[3]) * 1000)  # kHz → Hz, exact
                    if spot_hz == int(vfo_hz):
                        self._pota_tree.item(iid, tags=("tuned",))
                        continue
                except (ValueError, TypeError):
                    pass
            self._pota_tree.item(iid, tags=(base,))

    def _auto_refresh_pota(self):
        if self._pota_paused:
            return
        self._pota_status_lbl.config(text="Refreshing…", fg=FG2)
        threading.Thread(target=self._fetch_pota_spots, daemon=True).start()

    def _manual_pota_refresh(self):
        self._pota_status_lbl.config(text="Refreshing…", fg=FG2)
        if self._pota_after_id:
            self.after_cancel(self._pota_after_id)
            self._pota_after_id = None
        self._pota_loaded = True
        threading.Thread(target=self._fetch_pota_spots, daemon=True).start()

    def _toggle_pota_pause(self):
        self._pota_paused = not self._pota_paused
        if self._pota_paused:
            self._pota_pause_btn.config(text="▶ Resume Updates", fg=ACCENT)
            if self._pota_after_id:
                self.after_cancel(self._pota_after_id)
                self._pota_after_id = None
        else:
            self._pota_pause_btn.config(text="⏸ Pause Updates", fg=FG)
            self._pota_after_id = self.after(60_000, self._auto_refresh_pota)

    def _toggle_pota_scan(self):
        if self._pota_scan_active:
            self._stop_pota_scan()
        else:
            self._start_pota_scan()

    def _start_pota_scan(self):
        if not self._pota_tree.get_children():
            return
        self._pota_scan_active = True
        self._pota_scan_idx    = 0
        self._pota_scan_btn.config(text="⏹ Stop Scan", fg=ACCENT)
        self._pota_scan_step()

    def _stop_pota_scan(self):
        self._pota_scan_active = False
        self._pota_scan_btn.config(text="▶ Scan", fg=FG)
        if self._pota_scan_after_id:
            self.after_cancel(self._pota_scan_after_id)
            self._pota_scan_after_id = None

    def _pota_scan_step(self):
        if not self._pota_scan_active:
            return
        children = self._pota_tree.get_children()
        if not children:
            self._stop_pota_scan()
            return
        if self._pota_scan_idx >= len(children):
            self._pota_scan_idx = 0
        if self._pota_scan_skip_worked.get():
            try:
                rows = self.conn.execute(
                    "SELECT DISTINCT UPPER(TRIM(call)) FROM qso").fetchall()
                worked = {r[0] for r in rows if r[0]}
            except Exception:
                worked = set()
            for _ in range(len(children)):
                iid = children[self._pota_scan_idx]
                activator = str(self._pota_tree.item(iid, "values")[0]).strip().upper()
                if activator not in worked:
                    break
                self._pota_scan_idx = (self._pota_scan_idx + 1) % len(children)
        iid = children[self._pota_scan_idx]
        self._pota_tree.selection_set(iid)
        self._pota_tree.see(iid)
        self._pota_scan_idx += 1
        interval_ms = max(5, min(60, self._pota_scan_interval.get())) * 1_000
        self._pota_scan_after_id = self.after(interval_ms, self._pota_scan_step)

    # ── Entry form ────────────────────────────────────────────────────────
    def _build_entry_form(self, parent):
        ent = dict(bg=BG3, fg=FG, font=MONO, relief="flat",
                   insertbackground=ACCENT, bd=4)
        lbl_kw = dict(bg=BG, fg=FG2, font=SM, anchor="w")

        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=10, pady=(8,4))

        labels = ["Callsign *", "RST Sent", "RST Rcvd", "Park #", "Grid", "Comments", "Notes"]
        col_weights = [0, 0, 0, 0, 0, 1, 1]
        for i, (text, wt) in enumerate(zip(labels, col_weights)):
            tk.Label(f, text=text, **lbl_kw).grid(
                row=0, column=i, sticky="w",
                padx=(0 if i==0 else 10, 0))
            if wt:
                f.columnconfigure(i, weight=wt)

        self.e_call = tk.Entry(f, width=11, **ent)
        self.e_call.bind("<FocusOut>", self._on_call_focusout)
        self.e_call.bind("<Return>",   self._log_qso)
        self.e_call.grid(row=1, column=0, padx=(0,4), sticky="w")

        self.e_rst_s = tk.Entry(f, width=5, **ent)
        self.e_rst_s.insert(0,"59")
        self.e_rst_s.bind("<Return>", self._log_qso)
        self.e_rst_s.grid(row=1, column=1, padx=(10,4), sticky="w")

        self.e_rst_r = tk.Entry(f, width=5, **ent)
        self.e_rst_r.insert(0,"59")
        self.e_rst_r.bind("<Return>", self._log_qso)
        self.e_rst_r.grid(row=1, column=2, padx=(10,4), sticky="w")

        self.e_park = tk.Entry(f, width=11, **ent)
        self.e_park.bind("<Return>",   self._log_qso)
        self.e_park.bind("<FocusOut>", self._on_park_focusout)
        self.e_park.grid(row=1, column=3, padx=(10,4), sticky="w")

        self.e_grid = tk.Entry(f, width=7, **ent)
        self.e_grid.bind("<Return>", self._log_qso)
        self.e_grid.grid(row=1, column=4, padx=(10,4), sticky="w")

        self.e_comment = tk.Entry(f, width=22, **ent)
        self.e_comment.bind("<Return>", self._log_qso)
        self.e_comment.grid(row=1, column=5, padx=(10,4), sticky="ew")

        self.e_notes = tk.Entry(f, width=22, **ent)
        self.e_notes.bind("<Return>", self._log_qso)
        self.e_notes.grid(row=1, column=6, padx=(10,4), sticky="ew")

        info_row = tk.Frame(parent, bg=BG)
        info_row.pack(fill="x", padx=10, pady=(2,0))
        self._qrz_info_lbl = tk.Label(info_row, text="", bg=BG, fg=ACC3, font=SM,
                                       width=40, anchor="w")
        self._qrz_info_lbl.pack(side="left")
        self._park_info_lbl = tk.Label(info_row, text="", bg=BG, fg=MUTED, font=SM,
                                       width=50, anchor="w")
        self._park_info_lbl.pack(side="left", padx=(12, 0))
        self._rig_snap_lbl = tk.Label(info_row,
            text="Freq / Band / Mode will be captured from Flrig when LOG QSO is pressed.",
            bg=BG, fg=MUTED, font=SM)
        self._rig_snap_lbl.pack(side="right")

        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", padx=10, pady=(4,8))
        bc = dict(font=LBL, relief="flat", cursor="hand2", pady=5, padx=16)
        self._reticle_img = _make_reticle_img(20, BG, ACCENT)
        tk.Button(btn_row, text=" Snipe QSO", image=self._reticle_img,
                  compound="left", bg=ACCENT, fg=BG,
                  command=self._log_qso, **bc).pack(side="left")
        tk.Button(btn_row, text="✕ Clear Form", bg=BG3, fg=FG2,
                  command=self._clear_form, **bc).pack(side="left", padx=8)

        tk.Label(btn_row, text="Check for clear freq kHz:", bg=BG, fg=FG2, font=LBL).pack(side="left", padx=(12, 2))
        self._freq_check_border = tk.Frame(btn_row, bg=MUTED)
        self._freq_check_border.pack(side="left")
        freq_entry = tk.Entry(self._freq_check_border, textvariable=self._freq_check_var, width=7,
                              bg=BG2, fg=FG, insertbackground=FG, font=LBL,
                              bd=0, highlightthickness=0)
        freq_entry.pack(padx=3, pady=3)
        freq_entry.bind("<Return>", lambda _: self._check_freq_conflict())
        freq_entry.bind("<KeyRelease>", lambda e: self._reset_freq_border() if e.keysym != "Return" else None)

        self.e_call.focus_set()

    # ── Form helpers ──────────────────────────────────────────────────────
    def _on_call_focusout(self, _=None):
        call = self.e_call.get().strip().upper()
        if not call:
            return
        self.e_call.delete(0,"end")
        self.e_call.insert(0, call)
        if _qrz_session:
            threading.Thread(target=self._qrz_lookup_bg,
                             args=(call,), daemon=True).start()

    def _qrz_lookup_bg(self, call):
        info = qrz_lookup(call)
        if info:
            self.after(0, lambda: self._apply_qrz_info(info))

    def _apply_qrz_info(self, info):
        parts = [v for v in (info.get("name",""), info.get("qth",""),
                              info.get("grid","")) if v]
        self._qrz_info_lbl.config(
            text=("QRZ: " + "  ".join(parts)) if parts else "")

    def _on_park_focusout(self, _=None):
        ref = self.e_park.get().strip().upper()
        if not ref:
            self._park_info_lbl.config(text="", fg=MUTED)
            return
        self.e_park.delete(0, "end")
        self.e_park.insert(0, ref)
        if not parks_db_exists():
            self._park_info_lbl.config(
                text="Parks DB not built — use Settings > Update POTA Parks DB",
                fg=WARN)
            return
        self._park_info_lbl.config(text="Looking up…", fg=MUTED)
        threading.Thread(target=self._park_lookup_bg,
                         args=(ref,), daemon=True).start()

    def _park_lookup_bg(self, ref):
        info = lookup_park(ref)
        self.after(0, lambda: self._apply_park_info(ref, info))

    def _apply_park_info(self, ref, info):
        if info is None:
            self._park_info_lbl.config(text=f"Park {ref} not found in DB", fg=WARN)
            return
        grid  = info.get("grid", "")
        name  = info.get("name", "")
        state = info.get("state", "")
        if grid and not self.e_grid.get().strip():
            self.e_grid.delete(0, "end")
            self.e_grid.insert(0, grid[:4])
        parts = [p for p in (name, state) if p]
        label = " — ".join(parts) + (f"  [{grid}]" if grid else "") if parts else ref
        self._park_info_lbl.config(text=label, fg=ACC3)

    def _clear_form(self):
        for w in (self.e_call, self.e_park, self.e_grid, self.e_comment, self.e_notes):
            w.delete(0,"end")
        self.e_rst_s.delete(0,"end"); self.e_rst_s.insert(0,"59")
        self.e_rst_r.delete(0,"end"); self.e_rst_r.insert(0,"59")
        self._qrz_info_lbl.config(text="")
        self._park_info_lbl.config(text="", fg=MUTED)
        self._pota_spot_ctx = None
        self.e_call.focus_set()

    def _reset_freq_border(self):
        if self._freq_check_border is not None:
            self._freq_check_border.config(bg=MUTED)

    def _check_freq_conflict(self):
        if self._freq_check_border is None:
            return
        raw = self._freq_check_var.get().strip()
        if not raw:
            self._freq_check_border.config(bg=MUTED)
            return
        try:
            entered_khz = float(raw)
        except ValueError:
            self._freq_check_border.config(bg=MUTED)
            return
        valid = [float(s.get("frequency", s.get("freq", 0)))
                 for s in self._pota_spots_raw
                 if s.get("frequency", s.get("freq")) not in (None, "", 0)]
        if not valid:
            self._freq_check_border.config(bg=MUTED)
            return
        min_dist = min(abs(entered_khz - f) for f in valid)
        if min_dist < 1:
            color = WARN    # red   — 0.0–0.9 kHz
        elif min_dist < 2:
            color = ACCENT  # orange — 1.0–1.9 kHz
        elif min_dist < 3:
            color = YELLOW  # yellow — 2.0–2.9 kHz
        else:
            color = ACC3    # green  — 3.0+ kHz
        self._freq_check_border.config(bg=color)

    # ── Log QSO ───────────────────────────────────────────────────────────
    def _log_qso(self, _=None):
        if not self.adif_path:
            messagebox.showwarning("No Logbook", "Open or create a logbook first.")
            return
        call = self.e_call.get().strip().upper()
        if not call:
            messagebox.showerror("Required", "Callsign is required.")
            return

        now      = datetime.datetime.utcnow()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        if self._flrig_freq_hz is not None:
            try:
                freq_mhz = float(self._flrig_freq_hz) / 1_000_000
            except Exception:
                freq_mhz = float(self._flrig_freq_hz)
            band = freq_to_band(freq_mhz)
            mode = str(self._flrig_mode).upper() if self._flrig_mode else ""
        else:
            freq_mhz = None
            band     = ""
            mode     = ""

        row = {
            "call":       call,
            "date":       date_str,
            "time_on":    time_str,
            "freq":       freq_mhz,
            "band":       band,
            "mode":       mode,
            "rst_sent":   self.e_rst_s.get().strip() or "59",
            "rst_rcvd":   self.e_rst_r.get().strip() or "59",
            "name":       "",
            "qth":        "",
            "gridsquare": self.e_grid.get().strip().upper(),
            "park_nr":    self.e_park.get().strip(),
            "comment":    self.e_comment.get().strip(),
            "notes":      self.e_notes.get().strip(),
        }

        self.conn.execute("""
            INSERT INTO qso (call,date,time_on,freq,band,mode,
                rst_sent,rst_rcvd,name,qth,gridsquare,park_nr,comment,notes)
            VALUES (:call,:date,:time_on,:freq,:band,:mode,
                :rst_sent,:rst_rcvd,:name,:qth,:gridsquare,:park_nr,:comment,:notes)
        """, row)
        self.conn.commit()

        mycall = self.cfg.get("callsign","").upper()
        with open(self.adif_path, "a", encoding="utf-8") as f:
            f.write(row_to_adif(row, mycall))

        freq_disp = f"{freq_mhz:.4f} MHz" if freq_mhz else "freq unknown"
        snap = (f"Last: {call}  {date_str} {time_str}z  "
                f"{freq_disp}  {band}  {mode}")
        self._rig_snap_lbl.config(text=snap, fg=ACC3)
        self._set_status(f"Logged ✔  {call}  {date_str} {time_str}z  {freq_disp}  {band}  {mode}")
        self._reload_table()
        self._refresh_pota_highlights()
        self._maybe_post_pota_spot(row)
        self._clear_form()

    def _maybe_post_pota_spot(self, row):
        if not self._pota_respot_enabled.get():
            return
        ctx = self._pota_spot_ctx
        if not ctx:
            return
        mycall = self.cfg.get("callsign", "").upper()
        if not mycall:
            return
        freq_khz = ctx.get("freq_khz") or (
            str(round(row["freq"] * 1000)) if row.get("freq") else ""
        )
        mode = ctx.get("mode") or row.get("mode", "")
        def bg():
            ok, err = pota_post_spot(
                activator=ctx["activator"],
                spotter=mycall,
                reference=ctx["reference"],
                freq_khz=freq_khz,
                mode=mode,
            )
            msg = "POTA spot posted ✔" if ok else f"POTA spot failed: {err}"
            fg  = ACC3 if ok else WARN
            self.after(0, lambda: self._set_status(msg, fg))
        threading.Thread(target=bg, daemon=True).start()

    # ── Table ─────────────────────────────────────────────────────────────
    def _reload_table(self, rows=None):
        self._tree.delete(*self._tree.get_children())
        if rows is None:
            rows = self.conn.execute(
                "SELECT * FROM qso ORDER BY date DESC, time_on DESC").fetchall()
        for i, row in enumerate(rows):
            tag = "even" if i % 2 == 0 else "odd"
            freq_str = f"{row['freq']:.4f}" if row["freq"] else ""
            self._tree.insert("","end", iid=str(row["id"]),
                values=(row["id"], row["call"], row["date"], row["time_on"],
                        freq_str, row["band"] or "", row["mode"] or "",
                        row["rst_sent"] or "", row["rst_rcvd"] or "",
                        row["park_nr"] or "", row["comment"] or "",
                        row["notes"] or ""),
                tags=(tag,))
        n = len(rows)
        self._qso_count_lbl.config(text=f"{n} QSO{'s' if n!=1 else ''}")
        # Always attempt a map refresh; _refresh_map guards internally
        self._refresh_map()

    def _apply_filter(self):
        q    = self._search_var.get().strip()
        band = self._filter_band.get()
        mode = self._filter_mode.get()
        sql  = "SELECT * FROM qso WHERE 1=1"
        params = []
        if q:
            like = f"%{q}%"
            sql += (" AND (call LIKE ? OR comment LIKE ? OR notes LIKE ?"
                    " OR park_nr LIKE ?)")
            params += [like, like, like, like]
        if band and band != "All":
            sql += " AND band=?"; params.append(band)
        if mode and mode != "All":
            sql += " AND mode=?"; params.append(mode)
        sql += " ORDER BY date DESC, time_on DESC"
        self._reload_table(self.conn.execute(sql, params).fetchall())

    def _clear_filter(self):
        self._search_var.set("")
        self._filter_band.set("All")
        self._filter_mode.set("All")
        self._reload_table()

    def _sort_by(self, col):
        col_map = {
            "id":"id","Call":"call","Date":"date","Time UTC":"time_on",
            "Freq MHz":"freq","Band":"band","Mode":"mode",
            "RST Snt":"rst_sent","RST Rcv":"rst_rcvd","Park #":"park_nr",
            "Comments":"comment","Notes":"notes",
        }
        rows = self.conn.execute(
            f"SELECT * FROM qso ORDER BY {col_map.get(col,'id')} ASC").fetchall()
        self._reload_table(rows)

    def _on_qso_select(self, _=None):
        sel = self._tree.selection()
        if sel:
            row = self.conn.execute("SELECT * FROM qso WHERE id=?",
                                    (int(sel[0]),)).fetchone()
            if row:
                self._set_status(
                    f"Selected: {row['call']}  {row['date']} {row['time_on']}z"
                    f"  {row['band']}  {row['mode']}")

    # ── Edit / Delete ─────────────────────────────────────────────────────
    def _edit_qso(self, _=None):
        sel = self._tree.selection()
        if not sel:
            return
        row = self.conn.execute("SELECT * FROM qso WHERE id=?",
                                (int(sel[0]),)).fetchone()
        if row:
            EditDialog(self, self.conn, self.adif_path,
                       self.cfg.get("callsign",""), row, self._reload_table)

    def _delete_qso(self):
        sel = self._tree.selection()
        if not sel:
            return
        qso_id = int(sel[0])
        row = self.conn.execute("SELECT call,date FROM qso WHERE id=?",
                                (qso_id,)).fetchone()
        if not messagebox.askyesno("Delete",
                f"Delete QSO with {row['call']} on {row['date']}?"):
            return
        self.conn.execute("DELETE FROM qso WHERE id=?", (qso_id,))
        self.conn.commit()
        rewrite_adif(self.adif_path, self.conn,
                     self.cfg.get("callsign","").upper())
        self._reload_table()
        self._set_status(f"Deleted QSO #{qso_id}")

    # ── Logbook management ────────────────────────────────────────────────
    def _prompt_logbook(self):
        if messagebox.askyesno("POTA Hunter",
                "No logbook open.\nCreate a new logbook?"):
            self._new_logbook()
        else:
            self._choose_logbook()

    def _new_logbook(self):
        name = simpledialog.askstring("New Logbook",
            "Logbook name (e.g. General, POTA-2026):", parent=self)
        if not name:
            return
        safe = re.sub(r'[^\w\-]', '_', name.strip())
        path = os.path.join(LOGBOOK_DIR, safe + ".adi")
        if os.path.exists(path):
            if not messagebox.askyesno("Exists", f"{safe}.adi exists. Open it?"):
                return
        self._open_adif(path)

    def _choose_logbook(self):
        path = filedialog.askopenfilename(
            title="Open Logbook",
            initialdir=LOGBOOK_DIR,
            filetypes=[("ADIF Logbook","*.adi *.adif"),("All","*.*")])
        if path:
            self._open_adif(path)

    def _open_adif(self, path):
        self.adif_path = path
        if not os.path.exists(path):
            mycall = self.cfg.get("callsign","").upper()
            with open(path,"w",encoding="utf-8") as f:
                f.write(adif_header(mycall))
        count = load_adif_into_index(path, self.conn)
        name  = os.path.splitext(os.path.basename(path))[0]
        self._logbook_lbl.config(text=f"Logbook: {name}  [{path}]", fg=ACC3)
        self.title(f"POTA Hunter — {name}")
        self.cfg["last_logbook"] = path
        save_config(self.cfg)
        self._reload_table()
        self._set_status(f"Opened '{name}'  ({count} QSOs)")

    # ── ADIF export / import ──────────────────────────────────────────────
    def _export_adif(self):
        if not self.adif_path:
            messagebox.showwarning("No Logbook","Open a logbook first.")
            return
        dest = filedialog.asksaveasfilename(
            title="Export ADIF copy",
            defaultextension=".adi",
            filetypes=[("ADIF","*.adi *.adif"),("All","*.*")],
            initialdir=LOGBOOK_DIR)
        if not dest:
            return
        mycall = self.cfg.get("callsign","").upper()
        rewrite_adif(dest, self.conn, mycall)
        n = self.conn.execute("SELECT COUNT(*) FROM qso").fetchone()[0]
        self._set_status(f"Exported {n} QSOs → {os.path.basename(dest)}")
        messagebox.showinfo("Export Complete", f"Exported {n} QSOs to:\n{dest}")

    def _import_adif(self):
        if not self.adif_path:
            messagebox.showwarning("No Logbook","Open a logbook first.")
            return
        src = filedialog.askopenfilename(
            title="Import ADIF",
            filetypes=[("ADIF","*.adi *.adif"),("All","*.*")])
        if not src:
            return
        with open(src,"r",encoding="utf-8",errors="replace") as f:
            text = f.read()
        records = parse_adif_records(text)
        mycall  = self.cfg.get("callsign","").upper()
        for d in records:
            row = adif_to_row_dict(d)
            self.conn.execute("""
                INSERT INTO qso (call,date,time_on,freq,band,mode,
                    rst_sent,rst_rcvd,name,qth,gridsquare,park_nr,comment,notes)
                VALUES (:call,:date,:time_on,:freq,:band,:mode,
                    :rst_sent,:rst_rcvd,:name,:qth,:gridsquare,:park_nr,:comment,:notes)
            """, row)
        self.conn.commit()
        rewrite_adif(self.adif_path, self.conn, mycall)
        self._reload_table()
        self._set_status(f"Imported {len(records)} QSOs from {os.path.basename(src)}")
        messagebox.showinfo("Import Complete", f"Imported {len(records)} QSOs.")

    # ── Settings ──────────────────────────────────────────────────────────
    def _station_settings(self): StationDialog(self, self.cfg)
    def _qrz_settings(self):     QRZDialog(self, self.cfg)
    def _flrig_settings(self):   FlrigDialog(self, self.cfg)

    def _update_parks_db(self):
        if not messagebox.askyesno(
                "Update POTA Parks DB",
                "Download the latest POTA parks list from pota.app\n"
                "and rebuild the local database?\n\n"
                f"Destination: {PARKS_DB}"):
            return
        self._set_status("Downloading POTA parks list…")

        def _progress(msg):
            self.after(0, lambda: self._set_status(msg))

        def _worker():
            count, err = build_parks_db(progress_cb=_progress)
            if err:
                self.after(0, lambda: (
                    messagebox.showerror("Parks DB Error", err),
                    self._set_status(f"Parks DB update failed: {err}")))
            else:
                self.after(0, lambda: (
                    messagebox.showinfo(
                        "Parks DB Updated",
                        f"Downloaded and indexed {count:,} parks.\n"
                        f"Saved to: {PARKS_DB}"),
                    self._set_status(f"Parks DB updated — {count:,} parks.")))

        threading.Thread(target=_worker, daemon=True).start()

    def _check_parks_db_on_startup(self):
        if parks_db_exists():
            return
        if messagebox.askyesno(
                "POTA Parks Database",
                "The POTA parks database has not been built yet.\n\n"
                "Download it now? (~1 MB, runs in background)\n"
                f"Will be saved to: {PARKS_DB}"):
            self._update_parks_db()

    def _switch_theme(self):
        current = self.cfg.get("theme", "dark")
        self.cfg["theme"] = "light" if current == "dark" else "dark"
        save_config(self.cfg)
        _apply_palette(self.cfg["theme"])
        self._rebuild_ui()

    def _rebuild_ui(self):
        for attr in ("_flrig_poll_id", "_pota_after_id", "_map_resize_id"):
            id_ = getattr(self, attr, None)
            if id_:
                self.after_cancel(id_)
        if getattr(self, "_search_after_id", None):
            self.after_cancel(self._search_after_id)

        adif_path = self.adif_path

        for w in self.winfo_children():
            w.destroy()

        self.configure(bg=BG)
        self._flrig_freq_hz  = None
        self._flrig_mode     = None
        self._flrig_poll_id  = None
        self._flrig_polling  = False
        self._tune_suppress_until = 0.0
        self._pota_paused    = False
        self._pota_loaded    = False
        self._pota_after_id  = None
        self._pota_spots_raw = []
        self._pota_band_var  = tk.StringVar(value="All")
        self._pota_mode_var  = tk.StringVar(value="All")
        self._pota_hide_qrt  = tk.BooleanVar(value=False)
        self._pota_clicked_hz = None
        self._map_markers    = {}
        self._map_drawn      = False
        self._map_resize_id  = None
        self.adif_path       = ""

        self._style_ttk()
        self._build_menu()
        self._build_ui()

        if adif_path and os.path.exists(adif_path):
            self._open_adif(adif_path)

        if _qrz_session:
            self._qrz_lbl.config(text="QRZ: ✔", fg=ACC3)

        self._start_flrig_poll()

    # ── Flrig poll ────────────────────────────────────────────────────────
    def _start_flrig_poll(self):
        self._flrig_polling = False
        self._do_flrig_poll()

    def _do_flrig_poll(self):
        if not self._flrig_polling:
            self._flrig_polling = True
            host = self.cfg["flrig_host"]
            port = self.cfg["flrig_port"]
            def _fetch():
                freq_hz, mode, smeter, pwrmeter, ptt = flrig_get_all(host, port)
                self._flrig_polling = False
                self.after(0, lambda: self._update_vfo_display(
                    freq_hz, mode, smeter=smeter, pwrmeter=pwrmeter, ptt=ptt))
            threading.Thread(target=_fetch, daemon=True).start()
        self._flrig_poll_id = self.after(2000, self._do_flrig_poll)

    def _update_vfo_display(self, freq_hz, mode, force=False,
                            smeter=None, pwrmeter=None, ptt=False):
        suppressed = not force and time.monotonic() < self._tune_suppress_until
        if freq_hz is not None:
            if not suppressed:
                self._flrig_freq_hz = freq_hz
                self._flrig_mode    = mode
                try:
                    mhz = float(freq_hz) / 1_000_000
                except Exception:
                    mhz = float(freq_hz)
                band = freq_to_band(mhz)
                self._vfo_freq.config(text=f"{mhz:.4f} MHz", fg=ACCENT)
                self._vfo_mode.config(text=str(mode) if mode else "—", fg=ACC2)
                self._vfo_band.config(text=band if band else "—", fg=ACC3)
                self._refresh_pota_highlights()
            self._flrig_lbl.config(text="● Flrig: online", fg=ACC3)
            self._update_meter_display(smeter, pwrmeter, ptt)
        else:
            self._vfo_freq.config(text="—", fg=MUTED)
            self._vfo_mode.config(text="—", fg=MUTED)
            self._vfo_band.config(text="—", fg=MUTED)
            self._flrig_lbl.config(text="● Flrig: offline", fg=WARN)
            self._update_meter_display(None, None, False)

    def _update_meter_display(self, smeter, pwrmeter, ptt):
        is_tx = bool(ptt)
        if is_tx:
            raw = pwrmeter
            label = "PWR OUT"
        else:
            raw = smeter
            label = "S-METER"
        try:
            value = max(0, min(100, int(float(raw)))) if raw is not None else 0
        except (TypeError, ValueError):
            value = 0
        self._meter_value = value
        self._meter_is_tx = is_tx
        fg_color = WARN if is_tx else ACC3
        self._meter_type_lbl.config(text=label, fg=fg_color)
        self._meter_val_lbl.config(text=f"{value:3d}%", fg=fg_color)
        self._draw_meter_bar(value, is_tx)

    def _draw_meter_bar(self, value, is_tx):
        c = self._meter_canvas
        c.delete("all")
        w = c.winfo_width()
        if w < 10:
            return
        h = c.winfo_height()
        n_segs = 20
        gap = 2
        seg_w = (w - gap * (n_segs - 1)) / n_segs
        lit_count = round(value / 100 * n_segs)
        if is_tx:
            lit_color = "#dd3333"
            dim_color = "#2a0808"
        else:
            lit_color = "#22bb55"
            dim_color = "#062010"
        for i in range(n_segs):
            x1 = i * (seg_w + gap)
            x2 = x1 + seg_w
            color = lit_color if i < lit_count else dim_color
            c.create_rectangle(x1, 1, x2, h - 1, fill=color, outline="")

    # ── QRZ ───────────────────────────────────────────────────────────────
    def _qrz_login_bg(self):
        result = qrz_login(self.cfg["qrz_user"], self.cfg["qrz_pass"])
        if result is True:
            self.after(0, lambda: self._qrz_lbl.config(text="QRZ: ✔", fg=ACC3))
        else:
            self.after(0, lambda: self._qrz_lbl.config(text="QRZ: ✗", fg=WARN))

    # ── Misc ──────────────────────────────────────────────────────────────
    def _set_status(self, msg):
        self._status_var.set(msg)

    def _update_mycall_lbl(self):
        call = self.cfg.get("callsign","")
        self._mycall_lbl.config(text=call.upper() if call else "No callsign set")

    def _about(self):
        messagebox.showinfo("About POTA Hunter",
            "POTA Hunter v2.0 — POTA Activator Hunter & Ham Radio Logger\n\n"
            "Entry order:\n"
            "  Callsign → RST Sent → RST Rcvd\n"
            "  → Park # → Comments → Notes\n\n"
            "Date/Time  : UTC-stamped at moment of LOG QSO\n"
            "Freq/Band/Mode : captured from Flrig at LOG QSO\n"
            "Storage    : native ADIF (.adi) per logbook\n\n"
            "Logbooks folder: " + LOGBOOK_DIR)

    def destroy(self):
        if self._flrig_poll_id:
            self.after_cancel(self._flrig_poll_id)
        if self._pota_after_id:
            self.after_cancel(self._pota_after_id)
        if self._pota_scan_after_id:
            self.after_cancel(self._pota_scan_after_id)
        if self._map_resize_id:
            self.after_cancel(self._map_resize_id)
        if self.conn:
            self.conn.close()
        save_config(self.cfg)
        super().destroy()


# ══════════════════════════════════════════════════════════════════════════════
class EditDialog(tk.Toplevel):
    """Edit an existing QSO — rewrites ADIF on save."""
    def __init__(self, parent, conn, adif_path, mycall, row, refresh_cb):
        super().__init__(parent)
        self.title(f"Edit QSO #{row['id']} — {row['call']}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.conn       = conn
        self.adif_path  = adif_path
        self.mycall     = mycall
        self.row        = row
        self.refresh_cb = refresh_cb

        ent = dict(bg=BG3, fg=FG, font=MONO, relief="flat",
                   insertbackground=ACCENT, bd=4)
        lbl = dict(bg=BG, fg=FG2, font=SM)

        fields = [
            ("Callsign",          "call",        None),
            ("Date (YYYY-MM-DD)", "date",        None),
            ("Time UTC (HHMM)",   "time_on",     None),
            ("Freq (MHz)",        "freq",        None),
            ("Band",              "band",        "band"),
            ("Mode",              "mode",        "mode"),
            ("RST Sent",          "rst_sent",    None),
            ("RST Rcvd",          "rst_rcvd",    None),
            ("Name",              "name",        None),
            ("QTH",               "qth",         None),
            ("Grid Square",       "gridsquare",  None),
            ("Park #",            "park_nr",     None),
            ("Comments",          "comment",     None),
            ("Notes",             "notes",       None),
        ]
        self._entries = {}
        for i, (label, key, combo) in enumerate(fields):
            tk.Label(self, text=label, **lbl).grid(
                row=i, column=0, sticky="e", padx=(12,6), pady=3)
            if combo == "mode":
                w = ttk.Combobox(self, values=MODES, width=20, font=SM)
                w.set(row[key] or "")
            elif combo == "band":
                w = ttk.Combobox(self, values=BANDS, width=20, font=SM)
                w.set(row[key] or "")
            else:
                w = tk.Entry(self, width=26, **ent)
                val = row[key]
                if val is not None:
                    w.insert(0, str(val))
            w.grid(row=i, column=1, padx=(0,12), pady=3, sticky="w")
            self._entries[key] = w

        bf = tk.Frame(self, bg=BG)
        bf.grid(row=len(fields), column=0, columnspan=2, pady=10)
        bc = dict(font=SM, relief="flat", cursor="hand2", pady=4, padx=12)
        tk.Button(bf, text="✔ Save", bg=ACC3, fg=BG,
                  command=self._save, **bc).pack(side="left", padx=6)
        tk.Button(bf, text="✕ Cancel", bg=BG3, fg=FG,
                  command=self.destroy, **bc).pack(side="left")

    def _save(self):
        vals = {k: w.get().strip() for k, w in self._entries.items()}
        try:
            vals["freq"] = float(vals["freq"]) if vals["freq"] else None
        except ValueError:
            messagebox.showerror("Invalid","Frequency must be a number.",parent=self)
            return
        self.conn.execute("""
            UPDATE qso SET call=?,date=?,time_on=?,freq=?,band=?,mode=?,
            rst_sent=?,rst_rcvd=?,name=?,qth=?,gridsquare=?,park_nr=?,
            comment=?,notes=? WHERE id=?""",
            (vals["call"].upper(), vals["date"], vals["time_on"],
             vals["freq"], vals["band"], vals["mode"],
             vals["rst_sent"], vals["rst_rcvd"], vals["name"],
             vals["qth"], vals["gridsquare"], vals["park_nr"],
             vals["comment"], vals["notes"], self.row["id"]))
        self.conn.commit()
        rewrite_adif(self.adif_path, self.conn, self.mycall.upper())
        self.refresh_cb()
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
class StationDialog(tk.Toplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.title("Station Settings"); self.configure(bg=BG)
        self.resizable(False,False); self.grab_set()
        self.cfg=cfg; self.parent=parent
        ent=dict(bg=BG3,fg=FG,font=MONO,relief="flat",insertbackground=ACCENT,bd=4)
        lbl=dict(bg=BG,fg=FG2,font=SM)
        fields=[("My Callsign","callsign"),("Grid Square","gridsquare")]
        self._entries={}
        for i,(label,key) in enumerate(fields):
            tk.Label(self,text=label,**lbl).grid(row=i,column=0,
                sticky="e",padx=(12,6),pady=6)
            w=tk.Entry(self,width=20,**ent); w.insert(0,cfg.get(key,""))
            w.grid(row=i,column=1,padx=(0,12),pady=6); self._entries[key]=w
        bf=tk.Frame(self,bg=BG); bf.grid(row=2,column=0,columnspan=2,pady=10)
        bc=dict(font=SM,relief="flat",cursor="hand2",pady=4,padx=12)
        tk.Button(bf,text="✔ Save",bg=ACC3,fg=BG,command=self._save,**bc).pack(side="left",padx=6)
        tk.Button(bf,text="✕ Cancel",bg=BG3,fg=FG,command=self.destroy,**bc).pack(side="left")
    def _save(self):
        for key,w in self._entries.items():
            self.cfg[key]=w.get().strip().upper()
        save_config(self.cfg); self.parent._update_mycall_lbl(); self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
class QRZDialog(tk.Toplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.title("QRZ Login"); self.configure(bg=BG)
        self.resizable(False,False); self.grab_set()
        self.cfg=cfg; self.parent=parent
        ent=dict(bg=BG3,fg=FG,font=MONO,relief="flat",insertbackground=ACCENT,bd=4)
        lbl=dict(bg=BG,fg=FG2,font=SM)
        tk.Label(self,text="QRZ.com Username:",**lbl).grid(row=0,column=0,sticky="e",padx=(12,6),pady=6)
        self.e_user=tk.Entry(self,width=20,**ent); self.e_user.insert(0,cfg.get("qrz_user",""))
        self.e_user.grid(row=0,column=1,padx=(0,12),pady=6)
        tk.Label(self,text="QRZ.com Password:",**lbl).grid(row=1,column=0,sticky="e",padx=(12,6),pady=6)
        self.e_pass=tk.Entry(self,width=20,show="*",**ent); self.e_pass.insert(0,cfg.get("qrz_pass",""))
        self.e_pass.grid(row=1,column=1,padx=(0,12),pady=6)
        self._res=tk.Label(self,text="",bg=BG,fg=ACC3,font=SM)
        self._res.grid(row=2,column=0,columnspan=2)
        bf=tk.Frame(self,bg=BG); bf.grid(row=3,column=0,columnspan=2,pady=10)
        bc=dict(font=SM,relief="flat",cursor="hand2",pady=4,padx=12)
        tk.Button(bf,text="✔ Login & Save",bg=ACC3,fg=BG,command=self._login,**bc).pack(side="left",padx=6)
        tk.Button(bf,text="✕ Cancel",bg=BG3,fg=FG,command=self.destroy,**bc).pack(side="left")
    def _login(self):
        user=self.e_user.get().strip(); pw=self.e_pass.get().strip()
        self._res.config(text="Logging in…",fg=FG2)
        self.cfg["qrz_user"]=user; self.cfg["qrz_pass"]=pw; save_config(self.cfg)
        def bg():
            result=qrz_login(user,pw)
            if result is True:
                self.after(0,lambda:self._res.config(text="✔ Login successful!",fg=ACC3))
                self.after(0,lambda:self.parent._qrz_lbl.config(text="QRZ: ✔",fg=ACC3))
            else:
                self.after(0,lambda:self._res.config(text=f"✗ {result}",fg=WARN))
                self.after(0,lambda:self.parent._qrz_lbl.config(text="QRZ: ✗",fg=WARN))
        threading.Thread(target=bg,daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
class FlrigDialog(tk.Toplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.title("Flrig Settings"); self.configure(bg=BG)
        self.resizable(False,False); self.grab_set()
        self.cfg=cfg; self.parent=parent
        ent=dict(bg=BG3,fg=FG,font=MONO,relief="flat",insertbackground=ACCENT,bd=4)
        lbl=dict(bg=BG,fg=FG2,font=SM)
        tk.Label(self,text="Flrig Host:",**lbl).grid(row=0,column=0,sticky="e",padx=(12,6),pady=6)
        self.e_host=tk.Entry(self,width=20,**ent); self.e_host.insert(0,cfg.get("flrig_host","127.0.0.1"))
        self.e_host.grid(row=0,column=1,padx=(0,12),pady=6)
        tk.Label(self,text="Flrig Port:",**lbl).grid(row=1,column=0,sticky="e",padx=(12,6),pady=6)
        self.e_port=tk.Entry(self,width=8,**ent); self.e_port.insert(0,str(cfg.get("flrig_port",12345)))
        self.e_port.grid(row=1,column=1,padx=(0,12),pady=6,sticky="w")
        self._tl=tk.Label(self,text="",bg=BG,fg=FG2,font=SM)
        self._tl.grid(row=2,column=0,columnspan=2)
        bf=tk.Frame(self,bg=BG); bf.grid(row=3,column=0,columnspan=2,pady=10)
        bc=dict(font=SM,relief="flat",cursor="hand2",pady=4,padx=12)
        tk.Button(bf,text="⟳ Test",bg=BG4,fg=FG,command=self._test,**bc).pack(side="left",padx=6)
        tk.Button(bf,text="✔ Save",bg=ACC3,fg=BG,command=self._save,**bc).pack(side="left",padx=6)
        tk.Button(bf,text="✕ Cancel",bg=BG3,fg=FG,command=self.destroy,**bc).pack(side="left")
    def _test(self):
        host=self.e_host.get().strip()
        try: port=int(self.e_port.get().strip())
        except ValueError: self._tl.config(text="Invalid port",fg=WARN); return
        freq,mode=flrig_get(host,port)
        if freq is not None:
            try: mhz=float(freq)/1_000_000
            except Exception: mhz=freq
            self._tl.config(text=f"✔ Connected — {mhz:.4f} MHz  {mode}",fg=ACC3)
        else:
            self._tl.config(text="✗ Cannot reach Flrig",fg=WARN)
    def _save(self):
        self.cfg["flrig_host"]=self.e_host.get().strip()
        try: self.cfg["flrig_port"]=int(self.e_port.get().strip())
        except ValueError: pass
        save_config(self.cfg); self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _apply_palette(load_config().get("theme", "dark"))
    app = POTAHunter()
    app.mainloop()

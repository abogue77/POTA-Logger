#!/usr/bin/env python3
"""
HamLog — Ham Radio Station Logger  v2.0
Cross-platform (Windows & Linux).  Standard library only.
#Test message 
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
import os
import re
import json

# ── Paths ─────────────────────────────────────────────────────────────────────
LOGBOOK_DIR = os.path.join(os.path.expanduser("~"), "HamLog")
os.makedirs(LOGBOOK_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(LOGBOOK_DIR, "config.json")

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "callsign":   "",
    "gridsquare": "",
    "qrz_user":   "",
    "qrz_pass":   "",
    "flrig_host": "127.0.0.1",
    "flrig_port": 12345,
    "last_logbook": "",
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

# ── ADIF helpers ──────────────────────────────────────────────────────────────
def adif_field(tag, val):
    if val is None or str(val).strip() == "":
        return ""
    v = str(val).strip()
    return f"<{tag.upper()}:{len(v)}>{v} "

def adif_header(mycall):
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    prog = "HamLog"
    return (f"HamLog ADIF Log — {now}  Station: {mycall}\n"
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
        "gridsquare": d.get("GRIDSQUARE",""),
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
def flrig_get(host, port):
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/RPC2", allow_none=True)
        return proxy.rig.get_vfo(), proxy.rig.get_mode()
    except Exception:
        return None, None

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

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#111318"
BG2    = "#1a1d24"
BG3    = "#22262f"
BG4    = "#2a2f3a"
ACCENT = "#e8a020"
ACC2   = "#4fc3f7"
ACC3   = "#81c995"
WARN   = "#f28b82"
MUTED  = "#555e6e"
FG     = "#dde3ee"
FG2    = "#8c95a6"
SEL    = "#2d3a52"

MONO  = ("Courier New", 10)
DISP  = ("Courier New", 22, "bold")
SM    = ("Courier New", 9)
LBL   = ("Courier New", 10, "bold")
TITLE = ("Courier New", 13, "bold")

MODES = ["USB","LSB","SSB","AM","FM","FMN","CW","CWR","RTTY","RTTYR",
         "PKTUSB","PKTLSB","PKTFM","JS8","FT8","FT4","PSK31","OLIVIA","HELL"]
BANDS = ["160m","80m","60m","40m","30m","20m","17m","15m","12m","10m",
         "6m","2m","70cm","SAT","Other"]

# ══════════════════════════════════════════════════════════════════════════════
class HamLog(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HamLog")
        self.configure(bg=BG)
        self.minsize(1000, 640)
        self.resizable(True, True)

        self.cfg           = load_config()
        self.conn          = make_index()
        self.adif_path     = ""
        self._flrig_freq_hz = None
        self._flrig_mode    = None
        self._flrig_poll_id = None

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

    # ── TTK style ─────────────────────────────────────────────────────────
    def _style_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview", background=BG2, foreground=FG,
                    fieldbackground=BG2, rowheight=22, font=MONO)
        s.configure("Treeview.Heading", background=BG3, foreground=ACCENT,
                    font=LBL, relief="flat")
        s.map("Treeview", background=[("selected",SEL)],
                          foreground=[("selected",ACC2)])
        s.configure("TCombobox", fieldbackground=BG3, background=BG3,
                    foreground=FG, arrowcolor=ACCENT)
        s.map("TCombobox", fieldbackground=[("readonly",BG3)],
                           foreground=[("readonly",FG)])

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
        hm = menu("Help")
        hm.add_command(label="About", command=self._about)

    # ── Main UI ───────────────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG, pady=5)
        top.pack(fill="x", padx=14)
        tk.Label(top, text="◈ HamLog", bg=BG, fg=ACCENT, font=TITLE).pack(side="left")
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
        self._vfo_freq = tk.Label(vi, text="—", bg=BG2, fg=ACCENT, font=DISP)
        self._vfo_freq.grid(row=0,column=1,padx=(8,20))
        tk.Label(vi, text="MODE", bg=BG2, fg=MUTED, font=SM).grid(row=0,column=2)
        self._vfo_mode = tk.Label(vi, text="—", bg=BG2, fg=ACC2,
                                  font=("Courier New",18,"bold"))
        self._vfo_mode.grid(row=0,column=3,padx=(6,20))
        tk.Label(vi, text="BAND", bg=BG2, fg=MUTED, font=SM).grid(row=0,column=4)
        self._vfo_band = tk.Label(vi, text="—", bg=BG2, fg=ACC3,
                                  font=("Courier New",14,"bold"))
        self._vfo_band.grid(row=0,column=5,padx=(6,20))
        tk.Label(vi, text="← captured automatically on LOG QSO",
                 bg=BG2, fg=MUTED, font=SM).grid(row=0,column=6,padx=4)

        # Entry form
        form_frame = tk.LabelFrame(self, text=" NEW QSO ", bg=BG, fg=ACCENT,
                                   font=LBL, bd=1, relief="groove")
        form_frame.pack(fill="x", padx=14, pady=(0,6))
        self._build_entry_form(form_frame)

        # Search bar
        srch = tk.Frame(self, bg=BG)
        srch.pack(fill="x", padx=14, pady=(0,4))
        tk.Label(srch, text="SEARCH:", bg=BG, fg=FG2, font=LBL).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
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
        tbl = tk.Frame(self, bg=BG)
        tbl.pack(fill="both", expand=True, padx=14, pady=(0,8))
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
        self._tree.tag_configure("odd",  background=BG2)
        self._tree.tag_configure("even", background=BG3)
        self._tree.bind("<<TreeviewSelect>>", self._on_qso_select)
        self._tree.bind("<Double-1>",         self._edit_qso)
        self._tree.bind("<Delete>",    lambda _: self._delete_qso())

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

    # ── Entry form ────────────────────────────────────────────────────────
    def _build_entry_form(self, parent):
        ent = dict(bg=BG3, fg=FG, font=MONO, relief="flat",
                   insertbackground=ACCENT, bd=4)
        lbl_kw = dict(bg=BG, fg=FG2, font=SM, anchor="w")

        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=10, pady=(8,4))

        # Labels row
        labels = ["Callsign *", "RST Sent", "RST Rcvd", "Park #", "Comments", "Notes"]
        col_weights = [0, 0, 0, 0, 1, 1]
        for i, (text, wt) in enumerate(zip(labels, col_weights)):
            tk.Label(f, text=text, **lbl_kw).grid(
                row=0, column=i, sticky="w",
                padx=(0 if i==0 else 10, 0))
            if wt:
                f.columnconfigure(i, weight=wt)

        # Widgets row
        self.e_call = tk.Entry(f, width=11, **ent)
        self.e_call.bind("<FocusOut>", self._on_call_focusout)
        self.e_call.bind("<Return>",   self._on_call_focusout)
        self.e_call.grid(row=1, column=0, padx=(0,4), sticky="w")

        self.e_rst_s = tk.Entry(f, width=5, **ent)
        self.e_rst_s.insert(0,"59")
        self.e_rst_s.grid(row=1, column=1, padx=(10,4), sticky="w")

        self.e_rst_r = tk.Entry(f, width=5, **ent)
        self.e_rst_r.insert(0,"59")
        self.e_rst_r.grid(row=1, column=2, padx=(10,4), sticky="w")

        self.e_park = tk.Entry(f, width=11, **ent)
        self.e_park.grid(row=1, column=3, padx=(10,4), sticky="w")

        self.e_comment = tk.Entry(f, width=22, **ent)
        self.e_comment.grid(row=1, column=4, padx=(10,4), sticky="ew")

        self.e_notes = tk.Entry(f, width=22, **ent)
        self.e_notes.grid(row=1, column=5, padx=(10,4), sticky="ew")

        # Info line: QRZ result + last-logged rig snapshot
        info_row = tk.Frame(parent, bg=BG)
        info_row.pack(fill="x", padx=10, pady=(2,0))
        self._qrz_info_lbl = tk.Label(info_row, text="", bg=BG, fg=ACC3, font=SM)
        self._qrz_info_lbl.pack(side="left")
        self._rig_snap_lbl = tk.Label(info_row,
            text="Freq / Band / Mode will be captured from Flrig when LOG QSO is pressed.",
            bg=BG, fg=MUTED, font=SM)
        self._rig_snap_lbl.pack(side="right")

        # Buttons
        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", padx=10, pady=(4,8))
        bc = dict(font=LBL, relief="flat", cursor="hand2", pady=5, padx=16)
        tk.Button(btn_row, text="✚ LOG QSO", bg=ACCENT, fg=BG,
                  command=self._log_qso, **bc).pack(side="left")
        tk.Button(btn_row, text="✕ Clear Form", bg=BG3, fg=FG2,
                  command=self._clear_form, **bc).pack(side="left", padx=8)

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

    def _clear_form(self):
        for w in (self.e_call, self.e_park, self.e_comment, self.e_notes):
            w.delete(0,"end")
        self.e_rst_s.delete(0,"end"); self.e_rst_s.insert(0,"59")
        self.e_rst_r.delete(0,"end"); self.e_rst_r.insert(0,"59")
        self._qrz_info_lbl.config(text="")
        self.e_call.focus_set()

    # ── Log QSO ───────────────────────────────────────────────────────────
    def _log_qso(self):
        if not self.adif_path:
            messagebox.showwarning("No Logbook", "Open or create a logbook first.")
            return
        call = self.e_call.get().strip().upper()
        if not call:
            messagebox.showerror("Required", "Callsign is required.")
            return

        # UTC timestamp at the exact moment LOG is pressed
        now      = datetime.datetime.utcnow()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        # Pull freq/mode from Flrig right now
        freq_hz, rig_mode = flrig_get(self.cfg["flrig_host"],
                                       self.cfg["flrig_port"])
        if freq_hz is not None:
            try:
                freq_mhz = float(freq_hz) / 1_000_000
            except Exception:
                freq_mhz = float(freq_hz)
            band = freq_to_band(freq_mhz)
            mode = str(rig_mode).upper() if rig_mode else ""
        elif self._flrig_freq_hz is not None:
            # fall back to last polled value
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
            "gridsquare": "",
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

        # Append record to ADIF file immediately
        mycall = self.cfg.get("callsign","").upper()
        with open(self.adif_path, "a", encoding="utf-8") as f:
            f.write(row_to_adif(row, mycall))

        freq_disp = f"{freq_mhz:.4f} MHz" if freq_mhz else "freq unknown"
        snap = (f"Last: {call}  {date_str} {time_str}z  "
                f"{freq_disp}  {band}  {mode}")
        self._rig_snap_lbl.config(text=snap, fg=ACC3)
        self._set_status(f"Logged ✔  {call}  {date_str} {time_str}z  {freq_disp}  {band}  {mode}")
        self._reload_table()
        self._clear_form()

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
        if messagebox.askyesno("HamLog",
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
        self.title(f"HamLog — {name}")
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

    # ── Flrig poll ────────────────────────────────────────────────────────
    def _start_flrig_poll(self):
        self._do_flrig_poll()

    def _do_flrig_poll(self):
        freq_hz, mode = flrig_get(self.cfg["flrig_host"],
                                   self.cfg["flrig_port"])
        if freq_hz is not None:
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
            self._flrig_lbl.config(text="● Flrig: online", fg=ACC3)
        else:
            self._vfo_freq.config(text="—", fg=MUTED)
            self._vfo_mode.config(text="—", fg=MUTED)
            self._vfo_band.config(text="—", fg=MUTED)
            self._flrig_lbl.config(text="● Flrig: offline", fg=WARN)
        self._flrig_poll_id = self.after(2000, self._do_flrig_poll)

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
        messagebox.showinfo("About HamLog",
            "HamLog v2.0 — Ham Radio Station Logger\n\n"
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
    app = HamLog()
    app.mainloop()

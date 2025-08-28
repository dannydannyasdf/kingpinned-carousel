#!/usr/bin/env python3
"""
Turntable Matrix Navigator (visualizer + dual mini-terminals)

Fixes and improvements in this build:
- Range results: removed the "Signature:" line (no hash printed) as requested.
- Range performance (freeze fix): replaced per-record sequential queries with batched queries,
  dramatically reducing blocking time in the UI thread. No blocking input() is used.
- GRAPH focus arrow responsiveness retained. Focus starts on GRAPH (TAB toggles).
- Shift+Up/Down continues selection beyond the currently displayed range (auto-fetches more rows).
- Ctrl+Up/Down moves the cursor beyond visible range and extends the view when needed.
- Space toggles selection: Ctrl+Space toggles the current ordinal; Space alone toggles current char or active char-range.
- Selection Monitor (top-left) shows live aggregated selections (ordinals + char ranges).
- All commands (save/csv/json) operate over aggregated selection as well.

New in this update:
- X/Y axes represent geospatial Latitude/Longitude (labels updated).
- Camera reset (key 'R') also resets Geo to default: 43°41'13"N 79°18'21"W.
- In GRAPH focus: pressing Enter after selecting characters (range) or blocks (range)
  opens a SELECTION CONSOLE (bottom-right) populated with the selected chars/blocks and
  provides group-edit functionality:
    - groupedit field=<command|comments|sector_a>     (then type new value and Enter)
    - groupappend field=<...> <text to append>
    - clearselect  (clears selections)
  These operate on the currently aggregated selection (contiguous via Shift and/or toggled via Ctrl+Space).

Other features kept:
- Mini-terminal (bottom-left) handles all input. TAB toggles focus between TERMINAL and GRAPH.
- Terminal font zoom (F7/F8) and command termfont <N>.
- Palettes: palette green|blue|red, F6 cycles.
- Spokes/turns: spokes <int>, turns <float>, "view default" resets.
- Start view: current ordinal with previous and next (30 minutes total).

Dependencies:
- Python 3.8+, matplotlib, numpy
- clickhouse-client in PATH (password via CH_PASSWORD env var; default 'asdf').
"""

__version__ = "0.1.0"

import os
import sys
import time
import json
import math
import shutil
import signal
import threading
import subprocess
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# =========================
# ClickHouse client setup
# =========================
CH_PASSWORD = os.environ.get('CH_PASSWORD', 'asdf')
CH_CLIENT = shutil.which("clickhouse-client")
CLICKHOUSE_AVAILABLE = CH_CLIENT is not None

if not CLICKHOUSE_AVAILABLE:
    print("Warning: clickhouse-client not found in PATH. ClickHouse features will be disabled.")
    print("To install on Ubuntu/Debian: sudo apt-get install clickhouse-client")

if not os.environ.get('CH_PASSWORD'):
    print("Warning: CH_PASSWORD environment variable not set. Using default 'asdf'.")
    print("To set your password: export CH_PASSWORD='your_password_here'")


def run_clickhouse(query: str, fmt: str | None = None, timeout: int = 20) -> str:
    if not CLICKHOUSE_AVAILABLE:
        raise RuntimeError("ClickHouse client not available")
    
    env = os.environ.copy()
    env["CH_PASSWORD"] = CH_PASSWORD
    cmd = [CH_CLIENT, "--password", CH_PASSWORD, "--query", query]
    if fmt:
        cmd += ["--format", fmt]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            check=True, timeout=timeout
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(e.stderr.decode(errors="ignore") or "ClickHouse error")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ClickHouse query timed out")
    return proc.stdout.decode(errors="ignore")


def run_clickhouse_json(query: str, timeout: int = 20) -> list[dict]:
    if not CLICKHOUSE_AVAILABLE:
        return []
    
    try:
        out = run_clickhouse(query, fmt="JSONEachRow", timeout=timeout)
        rows: list[dict] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows
    except RuntimeError:
        return []


# =========================
# DB helpers
# =========================
def parse_compact_datetime(inp: str) -> str:
    l = len(inp)
    if l == 8:
        y, m, d = inp[:4], inp[4:6], inp[6:8]
        dt = f"{y}-{m}-{d} 13:15:05"
    elif l == 12:
        y, m, d, hh, mm = inp[:4], inp[4:6], inp[6:8], inp[8:10], inp[10:12]
        dt = f"{y}-{m}-{d} {hh}:{mm}:00"
    elif l == 14:
        y, m, d, hh, mm, ss = inp[:4], inp[4:6], inp[6:8], inp[8:10], inp[10:12], inp[12:14]
        dt = f"{y}-{m}-{d} {hh}:{mm}:{ss}"
    else:
        return "invalid"
    try:
        datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "invalid"
    return dt


def get_current_ordinal() -> int | None:
    rows = run_clickhouse_json(
        "SELECT ordinal FROM gamma_data WHERE id <= toUInt32(now()) ORDER BY id DESC LIMIT 1"
    )
    if not rows:
        return None
    return int(rows[0]['ordinal'])


def get_ordinal_by_timestamp(ts: int) -> int | None:
    rows = run_clickhouse_json(
        f"SELECT ordinal FROM gamma_data ORDER BY abs(id - {int(ts)}) ASC LIMIT 1"
    )
    if not rows:
        return None
    return int(rows[0]['ordinal'])


def get_id_for_ordinal(ordinal: int) -> int | None:
    rows = run_clickhouse_json(f"SELECT id FROM gamma_data WHERE ordinal = {int(ordinal)} LIMIT 1")
    if not rows:
        return None
    return int(rows[0]['id'])


def get_prev_next_ordinals(center_ordinal: int) -> tuple[int | None, int, int | None]:
    cid = get_id_for_ordinal(center_ordinal)
    if cid is None:
        return None, center_ordinal, None
    prev_rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE id < {cid} ORDER BY id DESC LIMIT 1")
    next_rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE id > {cid} ORDER BY id ASC LIMIT 1")
    prev_ord = int(prev_rows[0]['ordinal']) if prev_rows else None
    next_ord = int(next_rows[0]['ordinal']) if next_rows else None
    return prev_ord, center_ordinal, next_ord


def get_adjacent_ordinals(center_ordinal: int, older_count: int, newer_count: int) -> list[int]:
    cid = get_id_for_ordinal(center_ordinal)
    if cid is None:
        return [center_ordinal]
    prev_rows = run_clickhouse_json(
        f"SELECT ordinal FROM gamma_data WHERE id < {cid} ORDER BY id DESC LIMIT {int(max(0, older_count))}"
    )
    next_rows = run_clickhouse_json(
        f"SELECT ordinal FROM gamma_data WHERE id > {cid} ORDER BY id ASC LIMIT {int(max(0, newer_count))}"
    )
    prev_list_desc = [int(r['ordinal']) for r in prev_rows]
    next_list_asc = [int(r['ordinal']) for r in next_rows]
    return list(reversed(prev_list_desc)) + [center_ordinal] + next_list_asc


def get_next_older_than(ordinal: int) -> int | None:
    cid = get_id_for_ordinal(ordinal)
    if cid is None:
        return None
    rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE id < {cid} ORDER BY id DESC LIMIT 1")
    return int(rows[0]['ordinal']) if rows else None


def get_next_newer_than(ordinal: int) -> int | None:
    cid = get_id_for_ordinal(ordinal)
    if cid is None:
        return None
    rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE id > {cid} ORDER BY id ASC LIMIT 1")
    return int(rows[0]['ordinal']) if rows else None


def escape_sql_string(val: str) -> str:
    return val.replace("'", "''")


def generate_trunc_expr(field: str) -> str:
    return (
        f"CASE WHEN lengthUTF8({field}) > 8 THEN "
        f"concat(substringUTF8({field}, 1, 4), '..', substringUTF8({field}, -4)) "
        f"ELSE {field} END AS {field}"
    )


def make_display_query(ordinal: int, truncate: bool) -> str:
    if truncate:
        command_expr = generate_trunc_expr("command")
        comments_expr = generate_trunc_expr("comments")
        sector_a_expr = generate_trunc_expr("sector_a")
    else:
        command_expr = "command"
        comments_expr = "comments"
        sector_a_expr = "sector_a"
    return f"""
SELECT
  id,
  ordinal,
  formatDateTime(toDateTime(id), '%H:%i') AS groove_time,
  round((ordinal % 144) * 2.5, 2) AS phase,
  {command_expr},
  concat(
    substring('MonTueWedThuFriSatSun', (toDayOfWeek(toDateTime(id)) * 3) - 2, 3), ' ',
    substring('JanFebMarAprMayJunJulAugSepOctNovDec', (toMonth(toDateTime(id)) * 3) - 2, 3), ' ',
    toString(toDayOfMonth(toDateTime(id))), ' ', toString(toYear(toDateTime(id)))
  ) AS day_date,
  {comments_expr},
  {sector_a_expr},
  phase_a,
  sector_b,
  phase_b
FROM gamma_data
WHERE ordinal = {int(ordinal)}
"""


def visualize_wavepattern_text(sector_a: str, include_signature: bool = True) -> str:
    """
    Build wavepattern visualization text for a given sector_a.
    If include_signature is False, omits the "Signature: ..." line.
    """
    import hashlib
    h = hashlib.md5(sector_a.encode('utf-8')).hexdigest()[:16]
    last_digit = h[-1]
    polarity = "Positive" if (int(last_digit, 16) % 2 == 0) else "Negative"
    lines = []
    lines.append(f"Energy Wavepattern: {polarity}")
    if include_signature:
        lines.append(f"Signature: {h[:8]}-{h[8:16]}")
    # Bars (8 lines)
    for i in range(0, 16, 2):
        hex_pair = h[i:i+2]
        dec_val = int(hex_pair, 16)
        width = dec_val % 20 + 10
        char = "▲" if polarity == "Positive" else "▼"
        lines.append(char * width)
    lines.append("")
    return "\n".join(lines)


# =========================
# Navigator + Visualizer
# =========================
class TurntableMatrixNavigator:
    def __init__(self):
        # Focus: 'terminal' or 'graph'
        self.focus = "graph"  # default to GRAPH; TAB toggles

        # Terminal
        self.term_fontsize = 10
        self.input_buffer = ""
        self.input_cursor = 0
        self.input_mode = "normal"  # normal|goto|range|range_periods|edit_field|edit_value|spokes|turns|search|group_edit_value
        self.shell_output_text = ""

        # Selection monitor terminal content
        self.selection_output_text = "(no selection)"
        # Selection console (bottom-right)
        self.selection_console_visible = False
        self.selection_console_text = "(press Enter in GRAPH focus after selecting blocks/chars to open selection console)"
        self._group_field = None
        self._group_ordinals = []

        # Truncation
        self.truncate = True

        # View/Camera
        self.view_mode = "extended_period"  # start with 3 blocks (prev, current, next)
        self.zoom_level = 1.0
        self.elevation = 20
        self.azimuth = 60

        # Geo axes (lat/long)
        self.geo_enabled = True
        self.default_lat = 43 + 41/60 + 13/3600   # 43°41'13" N
        self.default_lon = -(79 + 18/60 + 21/3600)  # 79°18'21" W
        self.current_lat = self.default_lat
        self.current_lon = self.default_lon

        # Spokes/turns overrides
        self.user_spokes: int | None = None
        self.user_turns: float | None = None

        # Debounce
        self.key_debounce_delay = 0.03
        self._last_key_time = 0.0

        # Data/blocks
        self.center_ordinal: int | None = None
        self.time_blocks: list[tuple[str, str, int, int]] = []  # (period_label, key, ordinal, id_ts)
        self.current_block = 0
        self.current_char = 0

        # Contiguous selection (Shift)
        self.sel_anchor_ordinal: int | None = None
        self.sel_older_count = 0
        self.sel_newer_count = 0

        # Non-contiguous aggregation (Ctrl+Space)
        self.agg_selected_ordinals: list[int] = []  # order of addition preserved
        # Aggregated char selections per ordinal: dict[ordinal] -> list of (start_idx, end_idx) inclusive
        self.agg_selected_chars: dict[int, list[tuple[int, int]]] = {}

        # Char range selection within current block via Shift+Left/Right
        self.char_anchor_index: int | None = None
        self.char_range: tuple[int, int] | None = None

        # Monitor
        self.monitor_thread = None
        self.monitor_stop = threading.Event()
        self.monitor_fixed = False

        # Rain chars
        self.rain_chars = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾁﾂﾃ")

        # Palettes
        self.palettes = self._build_palettes()
        self.palette_name = "green"
        self.colors = self.palettes[self.palette_name]

        # MPL setup
        plt.style.use('dark_background')
        self.fig = plt.figure(figsize=(18, 12), facecolor='black')
        self.ax = self.fig.add_subplot(111, projection='3d', facecolor='black')
        self.cid = self.fig.canvas.mpl_connect('key_press_event', self.on_key_press)

        # Initial data: current ordinal with previous and next
        init_ord = get_current_ordinal()
        if init_ord is not None:
            self.center_ordinal = init_ord
            prev_ord, cur_ord, next_ord = get_prev_next_ordinals(init_ord)
            ordinals = [o for o in [prev_ord, cur_ord, next_ord] if o is not None]
            self.set_blocks_from_ordinals(ordinals)
            if cur_ord in [tb[2] for tb in self.time_blocks]:
                self.current_block = [tb[2] for tb in self.time_blocks].index(cur_ord)
            self.current_char = 0

        self.append_output("Ready. TAB toggles focus. In GRAPH focus, arrows navigate; Shift+Up/Down extends contiguous selection; Ctrl+Up/Down moves beyond view; Space toggles selection (Ctrl+Space toggles ordinal; Space alone toggles char-range). Press Enter to open SELECTION CONSOLE for group edits.")
        self.update_selection_terminal()
        self.render_all()

    # -------- Palettes --------
    def _build_palettes(self) -> dict:
        return {
            "green": {
                "MATRIX_GREEN": "#00FF00",
                "MATRIX_DARK_GREEN": "#008F11",
                "MATRIX_BRIGHT_GREEN": "#65FF65",
                "MATRIX_BLOCK": "#000000",
                "MATRIX_DIM_GREEN": "#004400",
                "MATRIX_BOOKEND": "#FF6B00",
                "MATRIX_TIME_MARKER": "#00FFFF",
            },
            "blue": {
                "MATRIX_GREEN": "#0000FF",
                "MATRIX_DARK_GREEN": "#00118F",
                "MATRIX_BRIGHT_GREEN": "#6565FF",
                "MATRIX_BLOCK": "#000000",
                "MATRIX_DIM_GREEN": "#000044",
                "MATRIX_BOOKEND": "#FFD700",
                "MATRIX_TIME_MARKER": "#8A2BE2",
            },
            "red": {
                "MATRIX_GREEN": "#FF0000",
                "MATRIX_DARK_GREEN": "#8F1100",
                "MATRIX_BRIGHT_GREEN": "#FF6565",
                "MATRIX_BLOCK": "#000000",
                "MATRIX_DIM_GREEN": "#440000",
                "MATRIX_BOOKEND": "#00FFFF",
                "MATRIX_TIME_MARKER": "#FFFF00",
            }
        }

    def set_palette(self, name: str | None = None, cycle: bool = False):
        names = ["green", "blue", "red"]
        if cycle:
            idx = names.index(self.palette_name)
            self.palette_name = names[(idx + 1) % len(names)]
        else:
            if name not in self.palettes:
                self.append_output(f"Unknown palette: {name}. Use: palette green|blue|red")
                return
            self.palette_name = name
        self.colors = self.palettes[self.palette_name]
        self.append_output(f"Palette set to {self.palette_name.upper()}")
        self.render_all()

    # -------- Geo helpers --------
    @staticmethod
    def _format_lat(lat: float) -> str:
        hemi = 'N' if lat >= 0 else 'S'
        lat = abs(lat)
        d = int(lat)
        m = int((lat - d) * 60)
        s = int(round(((lat - d) * 60 - m) * 60))
        return f"{d}°{m:02d}'{s:02d}\"{hemi}"

    @staticmethod
    def _format_lon(lon: float) -> str:
        hemi = 'E' if lon >= 0 else 'W'
        lon = abs(lon)
        d = int(lon)
        m = int((lon - d) * 60)
        s = int(round(((lon - d) * 60 - m) * 60))
        return f"{d}°{m:02d}'{s:02d}\"{hemi}"

    # -------- Prompts / terminals --------
    def default_prompt(self) -> str:
        return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Time requested: "

    def set_mode_prompt(self, mode: str) -> str:
        prompts = {
            "normal": self.default_prompt(),
            "goto": "Enter ordinal: ",
            "range": "Enter range (e.g., 874000-874003 or 874000,874002): ",
            "range_periods": "How many periods are included in the range? (e.g., 7): ",
            "edit_field": "Edit field (1=command,2=comments,3=sector_a, c=cancel): ",
            "edit_value": f"Enter new value: ",
            "spokes": "Enter spokes (integer, e.g., 64 or 144): ",
            "turns": "Enter turns (float or int, e.g., 3 or 7): ",
            "search": "Enter search pattern (or field:pattern): ",
            "group_edit_value": "Enter new value for group edit: ",
        }
        return prompts.get(mode, self.default_prompt())

    def prompt_with_caret(self) -> str:
        buf = self.input_buffer
        idx = max(0, min(len(buf), self.input_cursor))
        caret = "▌"
        return self.set_mode_prompt(self.input_mode) + buf[:idx] + caret + buf[idx:]

    # -------- Data fetching --------
    def fetch_blocks_for_ordinals(self, ordinals: list[int]) -> list[tuple[str, str, int, int]]:
        if not ordinals:
            return []
        ord_list = ",".join(str(int(x)) for x in ordinals)
        rows = run_clickhouse_json(f"""
SELECT
  ordinal,
  id,
  concat(formatDateTime(toDateTime(id - 600), '%H:%M'), '-', formatDateTime(toDateTime(id), '%H:%M')) AS period,
  sector_a
FROM gamma_data
WHERE ordinal IN ({ord_list})
ORDER BY id DESC
""")
        blocks = []
        for r in rows:
            period = r.get('period') or "??:??-??:??"
            key = r.get('sector_a') or ""
            ordinal = int(r.get('ordinal'))
            id_ts = int(r.get('id'))
            blocks.append((period, key, ordinal, id_ts))
        return blocks

    def set_blocks_from_ordinals(self, ordinals: list[int]):
        self.time_blocks = self.fetch_blocks_for_ordinals(ordinals)
        if not self.time_blocks:
            self.current_block = 0
            self.current_char = 0
            return
        if self.center_ordinal in [tb[2] for tb in self.time_blocks]:
            self.current_block = [tb[2] for tb in self.time_blocks].index(self.center_ordinal)
        else:
            self.current_block = 0
        self.current_char = min(self.current_char, max(0, len(self.time_blocks[self.current_block][1]) - 1))

    def reload_blocks_default(self):
        if self.view_mode == "single_block":
            ords = get_adjacent_ordinals(self.center_ordinal, 0, 0) if self.center_ordinal is not None else []
            self.set_blocks_from_ordinals(ords)
        elif self.view_mode == "multi_block":
            blocks = self.fetch_blocks_around(self.center_ordinal, 3)
            self.time_blocks = blocks
            if blocks:
                self.center_ordinal = blocks[0][2]
            self.current_block = 0
            self.current_char = 0
        else:
            if self.sel_anchor_ordinal is not None:
                ords = get_adjacent_ordinals(self.sel_anchor_ordinal, self.sel_older_count, self.sel_newer_count)
            else:
                base = self.center_ordinal if self.center_ordinal is not None else get_current_ordinal()
                if base is None:
                    self.time_blocks = []
                    return
                prev_o, cur_o, next_o = get_prev_next_ordinals(base)
                ords = [o for o in [prev_o, cur_o, next_o] if o is not None]
            self.set_blocks_from_ordinals(ords)

    def fetch_blocks_around(self, center_ordinal: int | None, count: int) -> list[tuple[str, str, int, int]]:
        if center_ordinal is None:
            center_ordinal = get_current_ordinal()
            if center_ordinal is None:
                return []
        rows = run_clickhouse_json(f"""
SELECT
  ordinal,
  id,
  concat(formatDateTime(toDateTime(id - 600), '%H:%M'), '-', formatDateTime(toDateTime(id), '%H:%M')) AS period,
  sector_a
FROM gamma_data
WHERE ordinal <= {int(center_ordinal)}
ORDER BY id DESC
LIMIT {int(count)}
""")
        blocks = []
        for r in rows:
            blocks.append((
                r.get('period') or "??:??-??:??",
                r.get('sector_a') or "",
                int(r.get('ordinal')),
                int(r.get('id'))
            ))
        return blocks

    # -------- Spokes/turns --------
    def calculate_spokes_config(self) -> tuple[int, float]:
        if self.user_spokes is not None or self.user_turns is not None:
            spokes = self.user_spokes if self.user_spokes is not None else 64
            turns = float(self.user_turns if self.user_turns is not None else 1.0)
            return max(8, int(spokes)), max(0.5, float(turns))
        blocks = max(1, len(self.time_blocks))
        if self.view_mode == "single_block":
            return 64, 1.0
        elif self.view_mode == "multi_block":
            target = 144
            spokes = target if blocks <= 3 else int(target * (blocks / 3.0))
            return max(32, spokes), float(blocks)
        else:
            return 144, float(blocks)

    # -------- Rendering --------
    def render_all(self):
        C = self.colors
        self.ax.clear()
        self.ax.set_facecolor('black')
        for a in [self.ax.xaxis.pane, self.ax.yaxis.pane, self.ax.zaxis.pane]:
            a.set_facecolor('black')
            a.set_edgecolor(C["MATRIX_DIM_GREEN"])
        self.ax.tick_params(colors=C["MATRIX_GREEN"], labelsize=10)

        self.render_digital_rain()

        spokes, turns_per_section = self.calculate_spokes_config()

        total_blocks = max(1, len(self.time_blocks))
        shaft_z = np.linspace(total_blocks * 10, 0, 200)
        self.ax.plot([0]*len(shaft_z), [0]*len(shaft_z), shaft_z, color=C["MATRIX_BRIGHT_GREEN"], linewidth=6, alpha=0.9)

        shaft_key = os.environ.get('SHAFT_KEY', CH_PASSWORD) or "#"
        shaft_chars = list(shaft_key)
        shaft_positions = np.linspace(total_blocks * 10 - 1, 1, len(shaft_chars))
        for i, (ch, z_pos) in enumerate(zip(shaft_chars, shaft_positions)):
            ang = (i / max(1, len(shaft_chars))) * turns_per_section * 2 * np.pi
            x = 0.4 * np.cos(ang); y = 0.4 * np.sin(ang)
            self.ax.text(x, y, z_pos, ch, fontsize=10 * self.zoom_level, ha='center', va='center',
                         fontfamily='monospace', weight='bold',
                         bbox=dict(boxstyle="circle,pad=0.2", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_GREEN"], linewidth=1.5),
                         color=C["MATRIX_GREEN"])

        matrix_colors = [C["MATRIX_GREEN"], C["MATRIX_BRIGHT_GREEN"], C["MATRIX_DARK_GREEN"]]
        time_ranges = []
        for i in range(len(self.time_blocks)):
            start = (len(self.time_blocks) - i) * 10
            end = start - 10
            time_ranges.append((start, end))

        max_radius = 4.8

        for section_idx, (period_label, key_string, ordinal, id_ts) in enumerate(self.time_blocks):
            chars = list(key_string)
            n = max(1, len(chars))
            start_time, end_time = time_ranges[section_idx]
            is_current_block = (section_idx == self.current_block)
            base_alpha = 1.0 if is_current_block else 0.35
            base_angle = section_idx * 2 * np.pi
            col = matrix_colors[section_idx % len(matrix_colors)]

            for i in range(n - 1):
                p1 = i / (n - 1) if n > 1 else 0
                p2 = (i + 1) / (n - 1) if n > 1 else 0
                z1 = start_time + (end_time - start_time) * p1
                z2 = start_time + (end_time - start_time) * p2
                a1 = base_angle + (i / max(1, spokes)) * turns_per_section * 2 * np.pi
                a2 = base_angle + ((i + 1) / max(1, spokes)) * turns_per_section * 2 * np.pi
                x1, y1 = max_radius * np.cos(a1), max_radius * np.sin(a1)
                x2, y2 = max_radius * np.cos(a2), max_radius * np.sin(a2)
                self.ax.plot([x1, x2], [y1, y2], [z1, z2], color=col, linewidth=2.5, alpha=base_alpha * 0.85)

            for i, ch in enumerate(chars):
                t = i / (n - 1) if n > 1 else 0
                z = start_time + (end_time - start_time) * t
                a = base_angle + (i / max(1, spokes)) * turns_per_section * 2 * np.pi
                x, y = max_radius * np.cos(a), max_radius * np.sin(a)

                is_current_char = (section_idx == self.current_block and i == self.current_char)
                is_bookend = i < 2 or i >= n - 2
                is_marker = (i % 6 == 0 and i >= 2 and i < n - 2)
                in_char_range = False
                if self.char_range and section_idx == self.current_block:
                    s, e = self.char_range
                    in_char_range = s <= i <= e

                if is_current_char:
                    c = C["MATRIX_BRIGHT_GREEN"]; bb = C["MATRIX_GREEN"]; fs = 15 * self.zoom_level
                    for off in [0.06, 0.12]:
                        self.ax.text(x, y, z, ch, fontsize=(fs + 3) * self.zoom_level, ha='center', va='center',
                                     fontfamily='monospace', weight='bold', color=C["MATRIX_GREEN"], alpha=0.35 * off)
                    self.ax.plot([0, x], [0, y], [z, z], color=C["MATRIX_BRIGHT_GREEN"], linewidth=6, alpha=1.0)
                elif in_char_range:
                    c = C["MATRIX_TIME_MARKER"]; bb = C["MATRIX_BLOCK"]; fs = 12 * self.zoom_level
                    self.ax.plot([0, x], [0, y], [z, z], color=C["MATRIX_TIME_MARKER"], linewidth=2.5, alpha=0.7, linestyle='-.')
                elif is_bookend:
                    c = C["MATRIX_BOOKEND"]; bb = C["MATRIX_BLOCK"]; fs = 13 * self.zoom_level
                    self.ax.plot([0, x], [0, y], [z, z], color=C["MATRIX_BOOKEND"], linewidth=2.0, alpha=0.75, linestyle=':')
                elif is_marker:
                    c = C["MATRIX_TIME_MARKER"]; bb = C["MATRIX_BLOCK"]; fs = 12 * self.zoom_level
                    self.ax.plot([0, x], [0, y], [z, z], color=C["MATRIX_TIME_MARKER"], linewidth=2, alpha=0.5, linestyle='-.')
                elif is_current_block:
                    c = C["MATRIX_GREEN"]; bb = C["MATRIX_DARK_GREEN"]; fs = 10 * self.zoom_level
                    self.ax.plot([0, x], [0, y], [z, z], color=col, linewidth=2, alpha=0.6)
                else:
                    c = C["MATRIX_DIM_GREEN"]; bb = C["MATRIX_BLOCK"]; fs = 8 * self.zoom_level
                    self.ax.plot([0, x], [0, y], [z, z], color=col, linewidth=1, alpha=0.15)

                self.ax.text(x, y, z, ch, fontsize=fs, ha='center', va='center',
                             fontfamily='monospace', weight='bold',
                             bbox=dict(boxstyle="round,pad=0.3", facecolor=bb, edgecolor=c, linewidth=1.2, alpha=0.85),
                             color=c)

        relay_points = [i * 10 for i in range(len(self.time_blocks) - 1, 0, -1)]
        for i, relay_z in enumerate(relay_points):
            angs = np.linspace(0, 2*np.pi, 60)
            rx = max_radius * 1.15 * np.cos(angs)
            ry = max_radius * 1.15 * np.sin(angs)
            rz = [relay_z] * len(angs)
            self.ax.plot(rx, ry, rz, color=C["MATRIX_BRIGHT_GREEN"], linewidth=3, alpha=0.9)
            self.ax.text(max_radius * 1.35, 0, relay_z, f'RELAY_{i+1:02d}',
                         fontsize=10 * self.zoom_level, ha='center', va='center', weight='bold', fontfamily='monospace',
                         bbox=dict(boxstyle="round,pad=0.3", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_BRIGHT_GREEN"], linewidth=1.2),
                         color=C["MATRIX_BRIGHT_GREEN"])

        for i, pos in enumerate([i * 10 - 5 for i in range(len(self.time_blocks), 0, -1)]):
            highlight = (i == self.current_block)
            label_color = C["MATRIX_BRIGHT_GREEN"] if highlight else [C["MATRIX_GREEN"], C["MATRIX_BRIGHT_GREEN"], C["MATRIX_DARK_GREEN"]][i % 3]
            self.ax.text(-max_radius * 1.3, 0, pos, f'BLOCK_{i+1:02d}',
                         fontsize=(14 if highlight else 11) * self.zoom_level, ha='center', va='center', weight='bold',
                         fontfamily='monospace',
                         bbox=dict(boxstyle="round,pad=0.3", facecolor=C["MATRIX_BLOCK"], edgecolor=label_color, linewidth=2 if highlight else 1),
                         color=label_color)

        current_key = self.time_blocks[self.current_block][1] if self.time_blocks else ""
        current_char_val = current_key[self.current_char] if (current_key and self.current_char < len(current_key)) else '—'
        if self.current_char < 2:
            char_type = "START BOOKEND"
        elif self.current_char >= max(0, len(current_key) - 2):
            char_type = "END BOOKEND"
        elif self.current_char % 6 == 0:
            char_type = "TIME MARKER (1min)"
        else:
            char_type = "DATA"

        # Geo axis labels
        self.ax.set_xlabel('Latitude (°)', fontsize=12 * self.zoom_level, weight='bold', color=C["MATRIX_GREEN"], fontfamily='monospace')
        self.ax.set_ylabel('Longitude (°)', fontsize=12 * self.zoom_level, weight='bold', color=C["MATRIX_GREEN"], fontfamily='monospace')
        self.ax.set_zlabel('TIME_AXIS', fontsize=12 * self.zoom_level, weight='bold', color=C["MATRIX_GREEN"], fontfamily='monospace')
        title = f'MATRIX CRYPTO NAVIGATOR - BLOCK:{self.current_block+1:02d} CHAR:{self.current_char+1:02d} [{current_char_val}] - {char_type} - VIEW: {self.view_mode.upper()}'
        self.ax.set_title(title, fontsize=14 * self.zoom_level, weight='bold', pad=20, color=C["MATRIX_BRIGHT_GREEN"], fontfamily='monospace')

        max_range = max_radius * 1.6 / max(0.01, self.zoom_level)
        self.ax.set_xlim([-max_range, max_range])
        self.ax.set_ylim([-max_range, max_range])
        self.ax.set_zlim([0, max(10, len(self.time_blocks) * 10)])
        z_ticks = [i * 10 for i in range(len(self.time_blocks), -1, -1)]
        z_labels = [f"{i:02d}:00" for i in range(len(self.time_blocks), -1, -1)]
        self.ax.set_zticks(z_ticks)
        self.ax.set_zticklabels(z_labels, fontfamily='monospace', color=C["MATRIX_GREEN"])
        self.ax.view_init(elev=self.elevation, azim=self.azimuth)
        self.ax.grid(True, alpha=0.25, color=C["MATRIX_DIM_GREEN"], linestyle='--')

        # Mini-terminals
        self.fig.texts.clear()
        # Selection monitor (top-left)
        self.fig.text(
            0.02, 0.98, self.selection_output_text,
            fontsize=self.term_fontsize, fontfamily='monospace',
            bbox=dict(boxstyle="round,pad=0.6", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_BRIGHT_GREEN"], linewidth=1.2, alpha=0.95),
            color=C["MATRIX_BRIGHT_GREEN"], verticalalignment='top', horizontalalignment='left'
        )
        # Shell mini-terminal (bottom-left)
        self.fig.text(
            0.02, 0.02, self.compose_terminal_text(),
            fontsize=self.term_fontsize, fontfamily='monospace',
            bbox=dict(boxstyle="round,pad=0.6", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_GREEN"], linewidth=1.2, alpha=0.95),
            color=C["MATRIX_GREEN"], verticalalignment='bottom', horizontalalignment='left'
        )
        # SELECTION CONSOLE (bottom-right)
        self.fig.text(
            0.98, 0.02, self.selection_console_text if self.selection_console_visible else " ",
            fontsize=self.term_fontsize, fontfamily='monospace',
            bbox=dict(boxstyle="round,pad=0.6", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_BRIGHT_GREEN"], linewidth=1.2, alpha=0.95),
            color=C["MATRIX_BRIGHT_GREEN"], verticalalignment='bottom', horizontalalignment='right'
        )
        # Crypto viewer (top-right)
        self.fig.text(
            0.98, 0.98, self.build_crypto_viewer(),
            fontsize=10, fontfamily='monospace',
            bbox=dict(boxstyle="round,pad=0.6", facecolor=C["MATRIX_BLOCK"], edgecolor=C["MATRIX_BRIGHT_GREEN"], linewidth=1.2, alpha=0.95),
            color=C["MATRIX_BRIGHT_GREEN"], verticalalignment='top', horizontalalignment='right'
        )

        plt.tight_layout()
        self.fig.canvas.draw_idle()

    def render_digital_rain(self):
        C = self.colors
        np.random.seed(42)
        for _ in range(16):
            x = np.random.uniform(-6, 6)
            y = np.random.uniform(-6, 6)
            z_positions = np.linspace(len(self.time_blocks) * 10, 0, 12)
            for z in z_positions:
                if np.random.random() < 0.28:
                    rain_char = np.random.choice(self.rain_chars)
                    alpha = np.random.uniform(0.08, 0.3)
                    self.ax.text(x, y, z, rain_char, fontsize=7 * self.zoom_level, ha='center', va='center',
                                 fontfamily='monospace', color=C["MATRIX_DIM_GREEN"], alpha=alpha)

    def build_crypto_viewer(self) -> str:
        if not self.time_blocks:
            cur_label = "—"; preview = ""; full_len = 0; pos = 0
        else:
            cur_label = self.time_blocks[self.current_block][0]
            key_full = self.time_blocks[self.current_block][1]
            full_len = len(key_full)
            start_idx = max(0, self.current_char - 10)
            end_idx = min(len(key_full), self.current_char + 11)
            snippet = key_full[start_idx:end_idx]
            pos_in = self.current_char - start_idx
            if 0 <= pos_in < len(snippet):
                lst = list(snippet); lst[pos_in] = f"[{lst[pos_in]}]"; snippet = "".join(lst)
            preview = snippet; pos = self.current_char + 1
        spokes, turns = self.calculate_spokes_config()
        return (
            "═══ CRYPTOGRAPHIC KEY VIEWER ═══\n"
            f"CURRENT BLOCK: {cur_label}\n"
            "KEY PREVIEW (±10 chars):\n"
            f"{preview}\n"
            f"FULL KEY LENGTH: {full_len}\n"
            f"POSITION: {pos}\n"
            f"VIEW MODE: {self.view_mode.upper()}\n"
            f"SPOKES: {spokes} | TURNS: {turns}\n"
        )

    def compose_terminal_text(self) -> str:
        C = self.colors
        cur_ord = (self.time_blocks[self.current_block][2] if self.time_blocks else None)
        blocks_ordinals = [tb[2] for tb in self.time_blocks]
        spokes, turns = self.calculate_spokes_config()
        sel_info = ""
        if self.sel_anchor_ordinal is not None:
            sel_info = f"Contiguous: anchor={self.sel_anchor_ordinal} older={self.sel_older_count} newer={self.sel_newer_count}"
        char_sel = ""
        if self.char_range:
            char_sel = f" | Char range: {self.char_range[0]}..{self.char_range[1]}"
        focus_str = f"FOCUS: {self.focus.upper()}"
        geo_str = f"Geo={self._format_lat(self.current_lat)} | {self._format_lon(self.current_lon)}"
        status = (
            f"{focus_str}  View={self.view_mode.upper()}  Trunc={'ON' if self.truncate else 'OFF'}  "
            f"Spokes={spokes} Turns={turns}  Blocks={len(self.time_blocks)}  "
            f"Current Ord={cur_ord}  RangeOrd={blocks_ordinals}{char_sel}\n"
            f"{sel_info}\n"
            f"{geo_str}\n"
            "----------------------------------------------------------------------------------------------------\n"
        )
        last = self.shell_output_text or "(no output)"
        prompt_line = self.prompt_with_caret() if self.focus == "terminal" else self.default_prompt()
        return status + last + ("\n" if not last.endswith("\n") else "") + "\n" + prompt_line

    def update_selection_terminal(self):
        lines = ["═══ SELECTION MONITOR ═══"]
        # Contiguous ordinals (Shift-based)
        contig = []
        if self.sel_anchor_ordinal is not None:
            contig = get_adjacent_ordinals(self.sel_anchor_ordinal, self.sel_older_count, self.sel_newer_count)
        # Aggregated ordinals (Ctrl+Space)
        agg = list(dict.fromkeys(self.agg_selected_ordinals))
        # Merge unique
        merged = list(dict.fromkeys(contig + agg))
        if merged:
            lines.append(f"Selected ordinals ({len(merged)}):")
            view = merged[:30]
            lines.append(", ".join(str(o) for o in view) + (" ..." if len(merged) > 30 else ""))
        else:
            lines.append("(no ordinals selected)")
        # Char selections
        if self.agg_selected_chars:
            lines.append("Selected chars per ordinal:")
            count_show = 0
            for ordn, ranges in list(self.agg_selected_chars.items())[:12]:
                human = "; ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in ranges[:8])
                more = " ..." if len(ranges) > 8 else ""
                lines.append(f"  {ordn}: {human}{more}")
                count_show += 1
            if len(self.agg_selected_chars) > count_show:
                lines.append(f"  ... and {len(self.agg_selected_chars) - count_show} more")
        if merged:
            lines.append("Commands 'save', 'csv', 'json' apply to ALL selected ordinals. Use 'clearselect' to reset.")
        self.selection_output_text = "\n".join(lines)

    def append_output(self, text: str):
        self.shell_output_text = (self.shell_output_text + ("\n" if self.shell_output_text else "") + text).strip()
        self.render_all()

    # -------- Key parsing helpers --------
    def parse_key(self, event):
        """
        Returns tuple: (base, is_shift, is_ctrl, is_space)
        base in {'up','down','left','right', 'space', other raw}
        """
        k = (event.key or "")
        low = k.lower()
        is_shift = ('shift+' in low) or ('shift' == low)
        is_ctrl = ('ctrl+' in low) or ('control' in low) or ('cmd+' in low)
        base = low.replace('shift+', '').replace('ctrl+', '').replace('cmd+', '')
        if base in ['up', 'down', 'left', 'right']:
            return base, is_shift, is_ctrl, False
        if base in ['space'] or k == ' ':
            return 'space', is_shift, is_ctrl, True
        return base, is_shift, is_ctrl, (k == ' ')

    # -------- Key handling --------
    def on_key_press(self, event):
        base, is_shift, is_ctrl, is_space = self.parse_key(event)

        # Focus toggle
        if base == 'tab':
            self.focus = "graph" if self.focus == "terminal" else "terminal"
            self.render_all()
            return

        # Palette cycle (F6)
        if (event.key or '').lower() == 'f6':
            self.set_palette(cycle=True)
            return

        # Terminal font zoom (F7/F8)
        if (event.key or '').lower() == 'f7':
            self.term_fontsize = min(28, self.term_fontsize + 1); self.render_all(); return
        if (event.key or '').lower() == 'f8':
            self.term_fontsize = max(6, self.term_fontsize - 1); self.render_all(); return

        # Camera reset -> also reset Geo to default lat/lon
        if event.key == 'R':
            self.elevation = 20; self.azimuth = 60; self.zoom_level = 1.0
            self.current_lat = self.default_lat; self.current_lon = self.default_lon
            self.render_all(); return

        if self.focus == "terminal":
            key = (event.key or "")
            low = key.lower()
            if len(key) == 1 and key.isprintable():
                idx = max(0, min(len(self.input_buffer), self.input_cursor))
                self.input_buffer = self.input_buffer[:idx] + key + self.input_buffer[idx:]
                self.input_cursor = idx + 1
                self.render_all()
                return
            if base == 'left':
                self.input_cursor = max(0, self.input_cursor - 1); self.render_all(); return
            if base == 'right':
                self.input_cursor = min(len(self.input_buffer), self.input_cursor + 1); self.render_all(); return
            if (low in ['enter', 'return']):
                self.handle_enter(); return
            if low == 'backspace':
                if self.input_cursor > 0 and self.input_buffer:
                    self.input_buffer = self.input_buffer[:self.input_cursor-1] + self.input_buffer[self.input_cursor:]
                    self.input_cursor -= 1
                self.render_all(); return
            if low == 'escape':
                self.input_buffer = ""; self.input_cursor = 0; self.input_mode = "normal"; self.render_all(); return
            if low == 't': self.goto_current_time(); return
            if low == 'g': self.input_mode = "goto"; self.input_buffer=""; self.input_cursor=0; self.render_all(); return
            if low == 'r': self.input_mode = "range"; self.input_buffer=""; self.input_cursor=0; self.render_all(); return
            if low == 'u': self.truncate = not self.truncate; self.refresh_shell_display_current(); return
            if low == 'h': self.shell_show_help(); return
            return

        # Focus == GRAPH
        # Enter -> populate SELECTION CONSOLE with current selection (blocks and/or char ranges)
        if (event.key or '').lower() in ['enter', 'return']:
            self.populate_selection_console()
            return

        # Selection toggling via SPACE
        if base == 'space':
            if is_ctrl:
                # toggle current block's ordinal in aggregated selection
                if not self.time_blocks: return
                ordn = self.time_blocks[self.current_block][2]
                if ordn in self.agg_selected_ordinals:
                    self.agg_selected_ordinals = [o for o in self.agg_selected_ordinals if o != ordn]
                else:
                    self.agg_selected_ordinals.append(ordn)
                self.update_selection_terminal(); self.render_all()
                return
            else:
                # toggle current char or current char_range into aggregated selections for this ordinal
                if not self.time_blocks: return
                ordn = self.time_blocks[self.current_block][2]
                n = len(self.time_blocks[self.current_block][1])
                if n == 0: return
                s, e = (self.char_range if self.char_range else (self.current_char, self.current_char))
                s = max(0, min(n-1, s)); e = max(0, min(n-1, e))
                if s > e: s, e = e, s
                ranges = self.agg_selected_chars.get(ordn, [])
                if (s, e) in ranges:
                    ranges = [r for r in ranges if r != (s, e)]
                else:
                    ranges.append((s, e))
                self.agg_selected_chars[ordn] = ranges
                self.update_selection_terminal(); self.render_all()
                return

        # Shift+Arrows => range/char-range extension
        if is_shift and base in ['up', 'down', 'left', 'right']:
            if base in ['up', 'down']:
                self.extend_contiguous_selection(base == 'up')
            else:
                self.extend_char_range(left=(base == 'left'))
            return

        # Ctrl+Up/Down => navigate beyond visible and extend if needed
        if is_ctrl and base in ['up', 'down']:
            self.ctrl_move_and_ensure_visible(older=(base == 'up'))
            return

        # Normal arrow navigation
        if base in ['up', 'down', 'left', 'right']:
            if base in ['up', 'down']:
                self.clear_char_selection()
            self.move_arrow(base)
            self.render_all()
            return

        # Zoom / view / camera
        low = (event.key or "").lower()
        if low in ['+', '=', '-','v','w','x','a','d']:
            if low in ['+', '=']: self.zoom_in()
            elif low == '-': self.zoom_out()
            elif low == 'v': self.cycle_view_mode()
            elif low == 'w': self.elevation = min(self.elevation + 5, 90)
            elif low == 'x': self.elevation = max(self.elevation - 5, -90)
            elif low == 'a': self.azimuth = (self.azimuth - 10) % 360
            elif low == 'd': self.azimuth = (self.azimuth + 10) % 360
            self.render_all(); return

        # Graph hotkeys (shell actions)
        if low == 't': self.goto_current_time(); return
        if low == 'g': self.input_mode = "goto"; self.input_buffer=""; self.input_cursor=0; self.render_all(); return
        if low == 'r': self.input_mode = "range"; self.input_buffer=""; self.input_cursor=0; self.render_all(); return
        if low == 'n': self.goto_plus_minus(1); return
        if low == 'p': self.goto_plus_minus(-1); return
        if low == 'm':
            if self.monitor_thread and self.monitor_thread.is_alive(): self.stop_monitor()
            else: self.start_monitor(fixed=False)
            return
        if low == 'f': self.start_monitor(fixed=True); return
        if low == 'e':
            if not self.time_blocks: return
            self._edit_for_ordinal = self.time_blocks[self.current_block][2]
            self.input_mode = "edit_field"; self.input_buffer=""; self.input_cursor=0; self.render_all(); return
        if low == 's': self.command_save(apply_to_all=True); return
        if low == 'c': self.command_csv(apply_to_all=True); return
        if low == 'j': self.command_json(apply_to_all=True); return
        if low == 'h': self.shell_show_help(); return
        if low == 'u': self.truncate = not self.truncate; self.refresh_shell_display_current(); return
        if low == 'q': plt.close(self.fig); os._exit(0)

    # -------- Populate Selection Console --------
    def populate_selection_console(self):
        """
        Build a detailed console view of the current selection (ordinals and/or char ranges).
        """
        lines = []
        lines.append("═══ SELECTION CONSOLE ═══")
        ords = self.aggregated_ordinals()
        if not ords:
            # fallback: current block
            ords = [self.time_blocks[self.current_block][2]] if self.time_blocks else []
        if not ords:
            lines.append("(no rows selected)")
        else:
            # Fetch block info for these ordinals (batched)
            ord_list = ",".join(str(int(o)) for o in ords)
            try:
                rows = run_clickhouse_json(f"""
SELECT
  ordinal,
  id,
  concat(formatDateTime(toDateTime(id - 600), '%H:%M'), '-', formatDateTime(toDateTime(id), '%H:%M')) AS period,
  sector_a,
  command,
  comments
FROM gamma_data
WHERE ordinal IN ({ord_list})
ORDER BY id DESC
""")
                # Index by ordinal
                info = {int(r['ordinal']): r for r in rows}
            except Exception:
                info = {}

            lines.append(f"Blocks selected ({len(ords)}):")
            for o in ords[:50]:
                r = info.get(o, {})
                per = r.get('period', '??:??-??:??')
                cmd = (r.get('command') or '')
                cmt = (r.get('comments') or '')
                lines.append(f"- Ord {o} [{per}] | cmd='{(cmd[:36]+'…' if len(cmd)>36 else cmd)}' | cmt='{(cmt[:36]+'…' if len(cmt)>36 else cmt)}'")

            # Char-ranges per selected ord (from agg + current block range if any)
            has_char = False
            char_map = dict(self.agg_selected_chars)
            # include current char_range if present and not already included
            if self.char_range and self.time_blocks:
                co = self.time_blocks[self.current_block][2]
                ranges = char_map.get(co, [])
                if self.char_range not in ranges:
                    ranges = ranges + [self.char_range]
                char_map[co] = ranges

            if char_map:
                lines.append("")
                lines.append("Character selections:")
                for ordn, ranges in list(char_map.items())[:30]:
                    r = info.get(ordn, {})
                    key = r.get('sector_a') or ''
                    if not key:
                        continue
                    has_char = True
                    rng_strs = []
                    for (a, b) in ranges[:10]:
                        a2, b2 = min(a, b), max(a, b)
                        frag = key[a2:b2+1] if 0 <= a2 < len(key) else ""
                        rng_strs.append(f"{a2}-{b2}:'{frag[:24]}{'…' if len(frag)>24 else ''}'")
                    lines.append(f"  Ord {ordn}: " + "; ".join(rng_strs) + (" ..." if len(ranges) > 10 else ""))

        lines.append("")
        lines.append("Group edit commands:")
        lines.append("  groupedit field=<command|comments|sector_a>   (then type value and Enter)")
        lines.append("  groupappend field=<command|comments|sector_a> <text to append>")
        lines.append("  clearselect")
        self.selection_console_text = "\n".join(lines)
        self.selection_console_visible = True
        self.render_all()

    # -------- Arrow and selection ops --------
    def move_arrow(self, base: str):
        if base == 'right':
            if not self.time_blocks: return
            n = len(self.time_blocks[self.current_block][1])
            if self.current_char < n - 1:
                self.current_char += 1
            elif self.current_block < len(self.time_blocks) - 1:
                self.current_block += 1; self.current_char = 0
        elif base == 'left':
            if not self.time_blocks: return
            if self.current_char > 0:
                self.current_char -= 1
            elif self.current_block > 0:
                self.current_block -= 1
                self.current_char = max(0, len(self.time_blocks[self.current_block][1]) - 1)
        elif base == 'up':
            if self.current_block > 0:
                old_len = max(1, len(self.time_blocks[self.current_block][1]))
                prog = self.current_char / (old_len - 1) if old_len > 1 else 0
                self.current_block -= 1
                new_len = max(1, len(self.time_blocks[self.current_block][1]))
                self.current_char = min(int(prog * (new_len - 1)), new_len - 1)
        elif base == 'down':
            if self.current_block < len(self.time_blocks) - 1:
                old_len = max(1, len(self.time_blocks[self.current_block][1]))
                prog = self.current_char / (old_len - 1) if old_len > 1 else 0
                self.current_block += 1
                new_len = max(1, len(self.time_blocks[self.current_block][1]))
                self.current_char = min(int(prog * (new_len - 1)), new_len - 1)

    def extend_contiguous_selection(self, older: bool):
        if not self.time_blocks:
            return
        self.view_mode = "extended_period"
        if self.sel_anchor_ordinal is None:
            self.sel_anchor_ordinal = self.time_blocks[self.current_block][2]
            self.sel_older_count = 0
            self.sel_newer_count = 0
        if older:
            self.sel_older_count += 1
        else:
            self.sel_newer_count += 1
        ords = get_adjacent_ordinals(self.sel_anchor_ordinal, self.sel_older_count, self.sel_newer_count)
        self.set_blocks_from_ordinals(ords)
        if self.sel_anchor_ordinal in [tb[2] for tb in self.time_blocks]:
            self.current_block = [tb[2] for tb in self.time_blocks].index(self.sel_anchor_ordinal)
        self.current_char = min(self.current_char, max(0, len(self.time_blocks[self.current_block][1]) - 1))
        self.update_selection_terminal()
        self.render_all()

    def extend_char_range(self, left: bool):
        if not self.time_blocks:
            return
        n = len(self.time_blocks[self.current_block][1])
        if n == 0:
            return
        if self.char_anchor_index is None:
            self.char_anchor_index = self.current_char
            self.char_range = (self.current_char, self.current_char)
        if left:
            self.current_char = max(0, self.current_char - 1)
        else:
            self.current_char = min(n - 1, self.current_char + 1)
        s, e = self.char_range or (self.current_char, self.current_char)
        s = min(s, self.current_char, self.char_anchor_index)
        e = max(e, self.current_char, self.char_anchor_index)
        self.char_range = (s, e)
        self.update_selection_terminal()
        self.render_all()

    def ctrl_move_and_ensure_visible(self, older: bool):
        if not self.time_blocks:
            return
        ord_current = self.time_blocks[self.current_block][2]
        target = get_next_older_than(ord_current) if older else get_next_newer_than(ord_current)
        if target is None:
            return
        visible_ordinals = [tb[2] for tb in self.time_blocks]
        if target not in visible_ordinals:
            if older:
                new_ordinals = [target] + visible_ordinals
            else:
                new_ordinals = visible_ordinals + [target]
            self.set_blocks_from_ordinals(new_ordinals)
        self.center_ordinal = target
        if target in [tb[2] for tb in self.time_blocks]:
            self.current_block = [tb[2] for tb in self.time_blocks].index(target)
            self.current_char = min(self.current_char, max(0, len(self.time_blocks[self.current_block][1]) - 1))
        self.update_selection_terminal()
        self.render_all()

    def clear_char_selection(self):
        self.char_anchor_index = None
        self.char_range = None
        self.update_selection_terminal()

    def clear_block_selection(self):
        self.sel_anchor_ordinal = None
        self.sel_older_count = 0
        self.sel_newer_count = 0
        self.update_selection_terminal()

    # -------- Zoom / view --------
    def zoom_in(self):
        self.zoom_level = min(self.zoom_level * 1.5, 8.0)

    def zoom_out(self):
        self.zoom_level = max(self.zoom_level / 1.5, 0.2)

    def cycle_view_mode(self):
        modes = ["single_block", "multi_block", "extended_period"]
        i = modes.index(self.view_mode)
        self.view_mode = modes[(i + 1) % len(modes)]
        if self.view_mode != "extended_period":
            self.clear_block_selection()
        self.reload_blocks_default()

    # -------- Shell actions and outputs --------
    def shell_show_help(self):
        help_text = """
HELP
- Enter commands at the prompt. Examples:
  id:123456
  874000        (ordinal)     | 874000u (untruncated)
  range 874000-874003         | pattern 874003,874000
  HH:MM or YYYY-MM-DD or YYYYMMDD[HHMM][SS]
  watch / watchu              | watch 874000
  find <pattern>              | field:pattern (command: comments: sector_a:)
  spokes 144                  | turns 7        | view default
  palette green|blue|red (F6 cycles) | termfont 12 (F7/F8 +/-)
  save / csv / json (apply to selection) | saveall / csvall / jsonall
  groupedit field=<command|comments|sector_a>
  groupappend field=<command|comments|sector_a> <text>
  clearselect                 | edit
  help / h                    | exit
- Focus and Navigation:
  TAB toggles focus (TERMINAL ↔ GRAPH)
  GRAPH focus:
    Arrows navigate; Shift+Up/Down extends contiguous selection; Shift+Left/Right extends char range
    Ctrl+Up/Down moves cursor by time and can pass visible bounds (auto-extends view)
    Space toggles selection (Ctrl+Space toggles ordinal; Space alone toggles char or char-range)
    Enter opens SELECTION CONSOLE for current selection and group edits
  t: current time  g: goto  r: range  n/p: next/prev
  m: monitor toggle  f: fixed monitor  e/s/c/j: edit/save/csv/json
  u: toggle truncation  V: cycle view  W/X/A/D: camera  R: camera & Geo reset
"""
        self.shell_output_text = help_text.strip()
        self.render_all()

    def shell_display_record(self, ordinal: int):
        try:
            pretty = run_clickhouse(make_display_query(ordinal, self.truncate), fmt="PrettyCompact")
            self.shell_output_text = f"--- Record {ordinal} ---\n{pretty}"
        except Exception as e:
            self.shell_output_text = f"Error displaying record {ordinal}: {e}"
        self.render_all()

    def display_range_and_set_view(self, range_spec: str, override_periods: int | None = None):
        """
        Efficient range display:
        - Single PrettyCompact query for all rows in range (no per-record loops blocking the UI)
        - Single JSON query to fetch sector_a for wavepatterns
        - No "Signature:" line in output per your request
        """
        ordinals = self.parse_range_spec(range_spec)
        if not ordinals:
            self.shell_output_text = f"Invalid range specification: {range_spec}"
            self.render_all()
            return

        # Cap for UI safety
        if len(ordinals) > 100:
            ordinals = ordinals[:100]

        ord_list = ",".join(str(int(o)) for o in ordinals)

        # Pretty output for all selected ordinals (batched)
        try:
            pretty_out = run_clickhouse(f"""
SELECT
  id,
  ordinal,
  formatDateTime(toDateTime(id), '%H:%i') AS groove_time,
  round((ordinal % 144) * 2.5, 2) AS phase,
  {generate_trunc_expr("command") if self.truncate else "command"} AS command,
  concat(
    substring('MonTueWedThuFriSatSun', (toDayOfWeek(toDateTime(id)) * 3) - 2, 3), ' ',
    substring('JanFebMarAprMayJunJulAugSepOctNovDec', (toMonth(toDateTime(id)) * 3) - 2, 3), ' ',
    toString(toDayOfMonth(toDateTime(id))), ' ', toString(toYear(toDateTime(id)))
  ) AS day_date,
  {generate_trunc_expr("comments") if self.truncate else "comments"} AS comments,
  {generate_trunc_expr("sector_a") if self.truncate else "sector_a"} AS sector_a,
  phase_a,
  sector_b,
  phase_b
FROM gamma_data
WHERE ordinal IN ({ord_list})
ORDER BY id DESC
""", fmt="PrettyCompact")
        except Exception as e:
            self.shell_output_text = f"Range display failed: {e}"
            self.render_all()
            return

        # Fetch sector_a per ordinal (batched) for wavepatterns (no signature)
        try:
            waves = run_clickhouse_json(f"""
SELECT ordinal, sector_a
FROM gamma_data
WHERE ordinal IN ({ord_list})
ORDER BY id DESC
""")
            ord_to_sector = {int(r['ordinal']): (r.get('sector_a') or "") for r in waves}
        except Exception:
            ord_to_sector = {}

        # Build output without "Signature:" line
        lines = [f"=== DISPLAYING {len(ordinals)} RECORDS (trunc={'on' if self.truncate else 'off'}) ===", ""]
        lines.append("── Records ─────────────────────────────────────────────────────────────────────────")
        lines.append(pretty_out.rstrip())
        if ord_to_sector:
            lines.append("")
            lines.append("── Energy Wavepatterns ───────────────────────────────────────────────────────────")
            for o in ordinals:
                s = ord_to_sector.get(o, "")
                if not s:
                    continue
                # include_signature=False per request
                vp = visualize_wavepattern_text(s, include_signature=False)
                # Prefix with ordinal header for clarity
                lines.append(f"[Ordinal {o}]")
                lines.append(vp.rstrip())
        self.shell_output_text = "\n".join(lines)

        # Update visual selection/view
        self.view_mode = "extended_period"
        self.set_blocks_from_ordinals(ordinals)
        if override_periods is not None and override_periods > 0:
            self.user_turns = float(override_periods)
        if self.time_blocks:
            self.sel_anchor_ordinal = self.time_blocks[0][2]
            self.sel_older_count = 0
            self.sel_newer_count = len(self.time_blocks) - 1
        self.current_block = 0
        self.current_char = 0
        self.update_selection_terminal()
        self.render_all()

    def shell_search_records(self, pattern: str):
        if not pattern:
            self.shell_output_text = "Error: Search pattern cannot be empty"
            self.render_all()
            return
        if pattern.startswith("command:"):
            fp = escape_sql_string(pattern.split(':', 1)[1]); hdr = "Searching command"
            cond = "command != ''" if fp == "!" else f"command ILIKE '%{fp}%'"
        elif pattern.startswith("comments:"):
            fp = escape_sql_string(pattern.split(':', 1)[1]); hdr = "Searching comments"
            cond = "comments != ''" if fp == "!" else f"comments ILIKE '%{fp}%'"
        elif pattern.startswith("sector_a:"):
            fp = escape_sql_string(pattern.split(':', 1)[1]); hdr = "Searching sector_a"
            cond = "sector_a != ''" if fp == "!" else f"sector_a ILIKE '%{fp}%'"
        else:
            sp = escape_sql_string(pattern); hdr = f"Searching for: {pattern}"
            cond = f"command ILIKE '%{sp}%' OR comments ILIKE '%{sp}%' OR sector_a ILIKE '%{sp}%'"
        q = f"""
SELECT ordinal, command, comments, sector_a
FROM gamma_data
WHERE {cond}
ORDER BY id DESC
LIMIT 100
"""
        try:
            rows = run_clickhouse_json(q)
        except Exception as e:
            self.shell_output_text = f"Search failed: {e}"
            self.render_all()
            return
        lines = [hdr, ""]
        lines.append("┌─────────┬──────────────┬──────────────────────────────────────────────────────┐")
        lines.append("│ ordinal │ field        │ match                                                │")
        lines.append("├─────────┼──────────────┼──────────────────────────────────────────────────────┤")
        count = 0
        for r in rows:
            ordn = str(r.get("ordinal", ""))
            for field in ["command", "comments", "sector_a"]:
                val = str(r.get(field, ""))
                if not val:
                    continue
                truncated = (val[:50] + ("..." if len(val) > 50 else ""))
                lines.append(f"│ {ordn:<7} │ {field:<12} │ {truncated:<52} │")
                count += 1
                break
        lines.append("└─────────┴──────────────┴──────────────────────────────────────────────────────┘")
        lines.append(f"Found {count} matching rows (displaying up to 100 most recent)")
        self.shell_output_text = "\n".join(lines)
        self.render_all()

    # -------- Commands over selection --------
    def aggregated_ordinals(self) -> list[int]:
        contig = get_adjacent_ordinals(self.sel_anchor_ordinal, self.sel_older_count, self.sel_newer_count) if self.sel_anchor_ordinal is not None else []
        agg = self.agg_selected_ordinals
        return list(dict.fromkeys(contig + agg)) or ([self.time_blocks[self.current_block][2]] if self.time_blocks else [])

    def command_save(self, apply_to_all: bool = False):
        ords = self.aggregated_ordinals() if apply_to_all else ([self.time_blocks[self.current_block][2]] if self.time_blocks else [])
        if not ords: return
        report = []
        for o in ords:
            filename = f"record_{o}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
            try:
                out = run_clickhouse(f"SELECT * FROM gamma_data WHERE ordinal = {o} FORMAT PrettyCompact", fmt=None)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(out)
                report.append(f"Saved: {filename}")
            except Exception as e:
                report.append(f"Save failed for {o}: {e}")
        self.shell_output_text = "\n".join(report)
        self.render_all()

    def command_csv(self, apply_to_all: bool = False):
        ords = self.aggregated_ordinals() if apply_to_all else ([self.time_blocks[self.current_block][2]] if self.time_blocks else [])
        if not ords: return
        report = []
        for o in ords:
            filename = f"record_{o}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            try:
                out = run_clickhouse(f"SELECT * FROM gamma_data WHERE ordinal = {o} FORMAT CSV", fmt=None)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(out)
                report.append(f"CSV: {filename}")
            except Exception as e:
                report.append(f"CSV failed for {o}: {e}")
        self.shell_output_text = "\n".join(report)
        self.render_all()

    def command_json(self, apply_to_all: bool = False):
        ords = self.aggregated_ordinals() if apply_to_all else ([self.time_blocks[self.current_block][2]] if self.time_blocks else [])
        if not ords: return
        try:
            rows = []
            for o in ords:
                rows.extend(run_clickhouse_json(f"SELECT * FROM gamma_data WHERE ordinal = {o} FORMAT JSONEachRow"))
            self.shell_output_text = "JSON (aggregated):\n" + json.dumps(rows, indent=2)
        except Exception as e:
            self.shell_output_text = f"JSON fetch failed: {e}"
        self.render_all()

    # -------- Command line (terminal) --------
    def handle_enter(self):
        s = self.input_buffer.strip()
        mode = self.input_mode
        self.input_buffer = ""
        self.input_cursor = 0

        if mode == "goto":
            if s.isdigit(): self.goto_ordinal(int(s))
            else: self.append_output(f"Invalid ordinal: {s}")
            self.input_mode = "normal"; return

        if mode == "range":
            if s: self._pending_range_spec = s; self.input_mode = "range_periods"
            else: self.input_mode = "normal"
            self.render_all(); return

        if mode == "range_periods":
            try: periods = int(s) if s else 0
            except Exception: periods = 0
            self.display_range_and_set_view(self._pending_range_spec, override_periods=periods if periods > 0 else None)
            self.input_mode = "normal"; self.render_all(); return

        if mode == "edit_field":
            fmap = {'1':'command','2':'comments','3':'sector_a'}
            if s.lower() == 'c':
                self.append_output("Edit cancelled."); self.input_mode = "normal"
            elif s in fmap:
                self._edit_field_name = fmap[s]; self.input_mode = "edit_value"
            else:
                self.append_output(f"Invalid selection: {s} (expect 1/2/3 or c)"); self.input_mode = "normal"
            self.render_all(); return

        if mode == "edit_value":
            if hasattr(self, "_edit_field_name") and hasattr(self, "_edit_for_ordinal") and self._edit_field_name and self._edit_for_ordinal is not None:
                self.apply_edit(self._edit_for_ordinal, self._edit_field_name, s)
            self._edit_field_name = None; self._edit_for_ordinal = None
            self.input_mode = "normal"; self.render_all(); return

        if mode == "spokes":
            if s.isdigit():
                self.user_spokes = max(8, int(s)); self.append_output(f"Spokes set to {self.user_spokes}")
            else:
                self.append_output(f"Invalid spokes: {s}")
            self.input_mode = "normal"; self.render_all(); return

        if mode == "turns":
            try:
                self.user_turns = max(0.5, float(s)); self.append_output(f"Turns set to {self.user_turns}")
            except Exception:
                self.append_output(f"Invalid turns: {s}")
            self.input_mode = "normal"; self.render_all(); return

        if mode == "search":
            if s: self.shell_search_records(s)
            self.input_mode = "normal"; self.render_all(); return

        if mode == "group_edit_value":
            if not self._group_field or not self._group_ordinals:
                self.append_output("No active group edit context."); self.input_mode = "normal"; return
            esc = escape_sql_string(s)
            ord_list = ",".join(str(int(o)) for o in self._group_ordinals)
            try:
                run_clickhouse(f"ALTER TABLE gamma_data UPDATE {self._group_field} = '{esc}' WHERE ordinal IN ({ord_list})", fmt=None)
                time.sleep(0.2)
                self.append_output(f"Group edit applied to field '{self._group_field}' for {len(self._group_ordinals)} rows.")
                self.reload_blocks_default()
                self.populate_selection_console()  # refresh console view
            except Exception as e:
                self.append_output(f"Group edit failed: {e}")
            finally:
                self._group_field = None
                self._group_ordinals = []
                self.input_mode = "normal"
            self.render_all(); return

        # Normal mode commands
        self.process_input_line(s)
        self.render_all()

    def process_input_line(self, s: str):
        if not s:
            self.goto_current_time(); return
        if s in ("help", "h"): self.shell_show_help(); return
        if s == "exit": plt.close(self.fig); os._exit(0)
        if s.startswith("spokes"):
            parts = s.split()
            if len(parts) == 2 and parts[1].isdigit():
                self.user_spokes = max(8, int(parts[1])); self.append_output(f"Spokes set to {self.user_spokes}")
            else:
                self.input_mode = "spokes"
            return
        if s.startswith("turns"):
            parts = s.split()
            if len(parts) == 2:
                try:
                    self.user_turns = max(0.5, float(parts[1])); self.append_output(f"Turns set to {self.user_turns}")
                except Exception:
                    self.input_mode = "turns"
            else:
                self.input_mode = "turns"
            return
        if s.strip() == "view default":
            self.user_spokes = None; self.user_turns = None
            self.append_output("View set to defaults (spokes/turns auto).")
            return
        if s.startswith("palette"):
            parts = s.split()
            if len(parts) == 1: self.set_palette(cycle=True)
            elif len(parts) == 2: self.set_palette(parts[1].lower())
            else: self.append_output("Usage: palette [green|blue|red]")
            return
        if s.startswith("termfont"):
            parts = s.split()
            if len(parts) == 2 and parts[1].isdigit():
                self.term_fontsize = max(6, min(28, int(parts[1]))); self.append_output(f"Terminal font size set to {self.term_fontsize}"); self.render_all()
            else:
                self.append_output("Usage: termfont <size>")
            return
        if s.startswith("find "): self.shell_search_records(s[5:].strip()); return
        if s == "watch": self.truncate = True; self.start_monitor(fixed=False); return
        if s == "watchu": self.truncate = False; self.start_monitor(fixed=False); return
        if s.startswith("watch "):
            rest = s.split(' ', 1)[1].strip()
            if rest.isdigit():
                self.truncate = True; self.center_ordinal = int(rest)
                self.view_mode = "single_block"; self.reload_blocks_default(); self.current_block = 0; self.current_char = 0
                self.start_monitor(fixed=True)
            else: self.append_output(f"Invalid ordinal for watch: {rest}")
            return
        if s.startswith("watchu "):
            rest = s.split(' ', 1)[1].strip()
            if rest.isdigit():
                self.truncate = False; self.center_ordinal = int(rest)
                self.view_mode = "single_block"; self.reload_blocks_default(); self.current_block = 0; self.current_char = 0
                self.start_monitor(fixed=True)
            else: self.append_output(f"Invalid ordinal for watchu: {rest}")
            return
        if s.startswith("rangeu "): self.truncate = False; self.display_range_and_set_view(s[len("rangeu "):].strip(), override_periods=None); return
        if s.startswith("patternu "): self.truncate = False; self.display_range_and_set_view(s[len("patternu "):].strip(), override_periods=None); return
        if s.startswith("range "): self.display_range_and_set_view(s[len("range "):].strip(), override_periods=None); return
        if s.startswith("pattern "): self.display_range_and_set_view(s[len("pattern "):].strip(), override_periods=None); return

        # Group edit commands
        if s.startswith("groupedit"):
            # Expect: groupedit field=<name>
            try:
                parts = s.split()
                field_part = next(p for p in parts[1:] if p.startswith("field="))
                field = field_part.split("=", 1)[1].strip()
            except Exception:
                self.append_output("Usage: groupedit field=<command|comments|sector_a>")
                return
            if field not in ("command", "comments", "sector_a"):
                self.append_output("Field must be one of: command, comments, sector_a")
                return
            ords = self.aggregated_ordinals()
            if not ords:
                self.append_output("No rows selected for group edit.")
                return
            self._group_field = field
            self._group_ordinals = ords
            self.input_mode = "group_edit_value"
            self.append_output(f"Group edit: set {field} for {len(ords)} rows. Type the new value and press Enter.")
            return

        if s.startswith("groupappend"):
            # Expect: groupappend field=<name> <text>
            try:
                parts = s.split()
                field_part = next(p for p in parts[1:] if p.startswith("field="))
                field = field_part.split("=", 1)[1].strip()
                # remaining text after field=...
                idx = s.index(field_part) + len(field_part)
                text = s[idx:].strip()
            except Exception:
                self.append_output("Usage: groupappend field=<command|comments|sector_a> <text to append>")
                return
            if field not in ("command", "comments", "sector_a"):
                self.append_output("Field must be one of: command, comments, sector_a")
                return
            ords = self.aggregated_ordinals()
            if not ords:
                self.append_output("No rows selected for group append.")
                return
            esc = escape_sql_string(text)
            ord_list = ",".join(str(int(o)) for o in ords)
            try:
                run_clickhouse(
                    f"ALTER TABLE gamma_data UPDATE {field} = concat(COALESCE({field}, ''), '{esc}') WHERE ordinal IN ({ord_list})",
                    fmt=None
                )
                time.sleep(0.2)
                self.append_output(f"Appended to '{field}' for {len(ords)} rows.")
                self.reload_blocks_default()
                self.populate_selection_console()
            except Exception as e:
                self.append_output(f"Group append failed: {e}")
            return

        if s == "csv": self.command_csv(apply_to_all=True); return
        if s == "save": self.command_save(apply_to_all=True); return
        if s == "json": self.command_json(apply_to_all=True); return
        if s == "csvall": self.command_csv(apply_to_all=True); return
        if s == "saveall": self.command_save(apply_to_all=True); return
        if s == "jsonall": self.command_json(apply_to_all=True); return
        if s == "clearselect":
            self.sel_anchor_ordinal = None; self.sel_older_count = 0; self.sel_newer_count = 0
            self.agg_selected_ordinals.clear(); self.agg_selected_chars.clear(); self.update_selection_terminal()
            self.selection_console_visible = False
            self.append_output("Selections cleared.")
            return
        if s == "edit":
            if not self.time_blocks: return
            self._edit_for_ordinal = self.time_blocks[self.current_block][2]
            self.input_mode = "edit_field"; return

        # 'u' suffix for local untruncated lookup
        local_trunc = self.truncate
        if s.endswith('u'):
            s = s[:-1]
            local_trunc = False
        prev_trunc = self.truncate
        self.truncate = local_trunc

        try:
            # id:<num>
            if s.startswith("id:") and s[3:].strip().isdigit():
                id_value = int(s[3:].strip())
                rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE id = {id_value} LIMIT 1")
                ordn = int(rows[0]['ordinal']) if rows else None
            # ordinal
            elif s.isdigit():
                rows = run_clickhouse_json(f"SELECT ordinal FROM gamma_data WHERE ordinal = {int(s)} LIMIT 1")
                ordn = int(rows[0]['ordinal']) if rows else None
            # date
            elif len(s) == 10 and s[4] == '-' and s[7] == '-':
                dt = f"{s} 13:15:05"; ts = int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").timestamp())
                ordn = get_ordinal_by_timestamp(ts)
            # time today
            elif (len(s) in (5, 8)) and s[2] == ':' and (len(s) == 5 or s[5] == ':'):
                today = datetime.now().strftime("%Y-%m-%d")
                dt = f"{today} {s if len(s)==8 else s+':00'}"
                ts = int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").timestamp())
                ordn = get_ordinal_by_timestamp(ts)
            # compact datetime
            elif s.isdigit() and len(s) in (8, 12, 14):
                dt = parse_compact_datetime(s)
                if dt == "invalid": ordn = None
                else:
                    ts = int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").timestamp())
                    ordn = get_ordinal_by_timestamp(ts)
            else:
                self.append_output("Invalid input format. Type 'help' for commands.")
                return

            if ordn is None:
                self.append_output("Error: No matching record found")
                return

            self.center_ordinal = ordn
            self.view_mode = "single_block"
            self.reload_blocks_default()
            self.current_block = 0; self.current_char = 0
            self.clear_block_selection()
            self.selection_console_visible = False
            self.shell_display_record(ordn)
        finally:
            self.truncate = prev_trunc

    # -------- Parsing helpers --------
    def parse_range_spec(self, spec: str) -> list[int]:
        spec = spec.strip()
        if not spec:
            return []
        ordinals: list[int] = []
        parts = [p.strip() for p in spec.split(',')]
        for seg in parts:
            if '-' in seg:
                start_s, end_s = seg.split('-', 1)
                step = 1
                if '+' in end_s:
                    end_s, step_s = end_s.split('+', 1)
                    if step_s.isdigit():
                        step = max(1, int(step_s))
                    else:
                        return []
                if not (start_s.isdigit() and end_s.isdigit()):
                    return []
                start_i = int(start_s); end_i = int(end_s)
                if start_i > end_i:
                    return []
                for i in range(start_i, end_i + 1, step):
                    ordinals.append(i)
            else:
                if not seg.isdigit():
                    return []
                ordinals.append(int(seg))
        return ordinals

    # -------- Jumps --------
    def goto_current_time(self):
        cur = get_current_ordinal()
        if cur is None:
            self.append_output("Error: Could not determine current ordinal."); return
        self.center_ordinal = cur
        self.view_mode = "single_block"
        self.reload_blocks_default()
        self.current_block = 0; self.current_char = 0
        self.clear_block_selection()
        self.selection_console_visible = False
        self.shell_display_record(cur)

    def goto_ordinal(self, ordn: int):
        self.center_ordinal = ordn
        self.view_mode = "single_block"
        self.reload_blocks_default()
        self.current_block = 0; self.current_char = 0
        self.clear_block_selection()
        self.selection_console_visible = False
        self.shell_display_record(ordn)

    def goto_plus_minus(self, delta: int):
        if not self.time_blocks: return
        new_ord = self.time_blocks[0][2] + delta
        self.goto_ordinal(new_ord)

    def refresh_shell_display_current(self):
        if not self.time_blocks: return
        self.shell_display_record(self.time_blocks[self.current_block][2])

    # -------- Edit --------
    def apply_edit(self, ordinal: int, field: str, new_value: str):
        esc = escape_sql_string(new_value)
        try:
            run_clickhouse(f"ALTER TABLE gamma_data UPDATE {field} = '{esc}' WHERE ordinal = {ordinal}", fmt=None)
            time.sleep(0.2)
            self.shell_output_text = f"Field '{field}' updated successfully for ordinal {ordinal}."
            self.reload_blocks_default()
        except Exception as e:
            self.shell_output_text = f"Error updating field: {e}"

    # -------- Monitor --------
    def start_monitor(self, fixed: bool):
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.shell_output_text = "Monitor already running."; self.render_all(); return
        self.monitor_stop.clear()
        self.monitor_fixed = fixed
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.shell_output_text = f"Monitoring started ({'fixed' if fixed else 'current'}). Press 'm' to stop."
        self.render_all()

    def _monitor_loop(self):
        iteration = 0
        while not self.monitor_stop.is_set():
            try:
                if self.monitor_fixed:
                    ord_to_show = self.time_blocks[self.current_block][2] if self.time_blocks else self.center_ordinal or get_current_ordinal()
                else:
                    ord_to_show = get_current_ordinal()
                    if ord_to_show is not None:
                        self.center_ordinal = ord_to_show
                if ord_to_show is not None:
                    self.view_mode = "single_block"
                    self.reload_blocks_default()
                    self.current_block = 0; self.current_char = 0
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    pretty = run_clickhouse(make_display_query(ord_to_show, self.truncate), fmt="PrettyCompact")
                    self.shell_output_text = f"MONITOR [{'FIXED' if self.monitor_fixed else 'CURRENT'}] iter={iteration+1}\nCurrent time: {now_str}\n\n{pretty}"
                    self.render_all()
                sleep_t = 1.0 if iteration < 10 else 7.0
                iteration += 1
                for _ in range(int(sleep_t * 10)):
                    if self.monitor_stop.is_set(): break
                    time.sleep(0.1)
            except Exception as e:
                self.shell_output_text = f"Monitor error: {e}"; self.render_all(); time.sleep(2.0)

    def stop_monitor(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_stop.set()
            self.monitor_thread.join(timeout=2.0)
            self.monitor_thread = None
            self.shell_output_text = "Monitor stopped."
            self.render_all()


# =========================
# Entry point
# =========================
def main():
    print(f"Kingpinned Carousel {__version__}")
    
    if CLICKHOUSE_AVAILABLE:
        try:
            run_clickhouse("SELECT 1", fmt=None)
            print("ClickHouse connectivity: OK")
        except Exception as e:
            print(f"Warning: ClickHouse connectivity problem: {e}")
    else:
        print("ClickHouse features disabled (client not available)")

    nav = TurntableMatrixNavigator()

    def sigint_handler(sig, frame):
        try: plt.close(nav.fig)
        except Exception: pass
        os._exit(0)

    signal.signal(signal.SIGINT, sigint_handler)
    plt.show()


if __name__ == "__main__":
    main()

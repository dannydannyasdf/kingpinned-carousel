# Kingpinned Carousel / Turntable Matrix Navigator (V1)

This project visualizes time-ordered records (from ClickHouse) as a 3D "carousel" around a time spindle. Dual-focus input:
- GRAPH focus: arrows navigate; Shift extends contiguous selection; Ctrl+Space toggles ordinals; Space toggles char/char-range; Enter opens Selection Console.
- TERMINAL focus: type commands; Enter submits; TAB toggles focus.

Status
- Initial import. Performance and aggregated selection verification next.

Requirements
- Python 3.10+ (Linux recommended)
- clickhouse-client in PATH (export CH_PASSWORD if needed)
- pip install -r requirements.txt

Quick start
1) Install dependencies:
   pip install -r requirements.txt
2) Run:
   python3 turntable_keysGit96.py
3) Keys (GRAPH focus):
   - TAB: toggle focus
   - Arrows: navigate
   - Shift+Up/Down: extend contiguous block selection
   - Shift+Left/Right: extend char-range in current block
   - Ctrl+Space: toggle current ordinal in aggregated selection
   - Space: toggle current char or char-range for current ordinal
   - Enter: open Selection Console
   - R: reset camera + set selection window to current-3..current+4
   - F6/F7/F8: palette cycle / terminal font zoom
   - u: toggle truncation
4) Terminal commands (TERMINAL focus):
   - goto (ordinal/time); range; pattern; save/csv/json; spokes <int>; turns <float>; help

Next steps
- Verify aggregated selection responsiveness and correctness.
- Profile range display and rendering; convert per-record loops to batched queries where needed.
- Add geospatial axis mapping defaults (lat/lon) if not present in this version.
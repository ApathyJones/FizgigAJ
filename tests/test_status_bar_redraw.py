"""Status bar: repaint only when the picture actually changes.

_poll_status_bar fires every second for the life of the process, and _draw_status_segment drew
its gradient as one canvas rectangle per 3 px — a few hundred items per bar. It rebuilt all of
them every tick even when the reading hadn't moved, which on an idle app is most ticks, and the
result was pixel-identical each time.

The guard keys on what is actually drawn (geometry, fill width in whole pixels, peak tick, and
the label at its displayed precision), so a sub-pixel wobble in the raw byte count is correctly
treated as no change while anything visible still repaints.

Run: venv/Scripts/python.exe tests/test_status_bar_redraw.py
"""
import os
import sys
import tempfile

os.environ["FIZGIG_NO_PERSIST"] = "1"          # traced vars auto-save; never touch real prefs
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "src"))

import tkinter as tk  # noqa: E402
import lora_trainer_gui as G  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

G.LAST_USED_FILE = os.path.join(tempfile.gettempdir(), "nope", ".last_used.json")

fails = []


def ck(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail else ''}")
    if not cond:
        fails.append(label)


root = tk.Tk()
root.withdraw()
gui = G.LoRATrainerGUI(root)

canvas = tk.Canvas(root, width=300, height=18)
GB = 1073741824
GREEN, RED = "#3FB950", "#E5534B"


def draw(used, total=32 * GB, peak=0):
    gui._draw_status_segment(canvas, used, total, peak, "VRAM", GREEN, RED)


# --- 1. the first paint actually draws --------------------------------------------------
draw(8 * GB)
items = canvas.find_all()
# 8/32 GB over 300 px is a 75 px fill, drawn one rectangle per 3 px, plus the track and the
# label: 27 items. That per-3px cost is the whole reason repainting an unchanged bar mattered.
ck("first paint draws the bar", len(items) == 27, f"{len(items)} canvas items")
ck("  a fuller bar costs proportionally more items",
   len(items) < 52, "50% fill draws 52; see below")

# --- 2. an identical reading repaints nothing --------------------------------------------
# Canvas item ids are monotonic, so an unchanged tuple proves nothing was deleted and recreated.
for _ in range(10):
    draw(8 * GB)
ck("10 identical ticks recreate nothing", canvas.find_all() == items,
   f"{len(canvas.find_all())} items now")

# --- 3. a change that IS visible still repaints ------------------------------------------
draw(16 * GB)
moved = canvas.find_all()
ck("a real change repaints", moved != items, f"{len(moved)} items")
ck("  and the label followed the value",
   any("16.0" in str(canvas.itemcget(i, "text")) for i in moved
       if canvas.type(i) == "text"))

# --- 4. a sub-pixel wobble is correctly ignored -------------------------------------------
# 300 px over 32 GB is ~109 MB per pixel; a few hundred KB moves neither the bar nor the label.
before = canvas.find_all()
draw(16 * GB + 200000)
ck("a sub-pixel change is skipped", canvas.find_all() == before)

# --- 5. the peak tick participates in the signature ---------------------------------------
before = canvas.find_all()
draw(16 * GB, peak=24 * GB)
ck("a moved peak tick repaints", canvas.find_all() != before)

# --- 6. the guard is per-canvas, not global ------------------------------------------------
other = tk.Canvas(root, width=300, height=18)
gui._draw_status_segment(other, 8 * GB, 32 * GB, 0, "RAM", "#3B82F6", "#EAC54F")
ck("a second canvas draws independently", len(other.find_all()) > 10,
   f"{len(other.find_all())} items")

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

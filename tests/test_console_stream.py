"""Training console streaming: carriage-return progress bars and the line cap.

tqdm rewrites ONE line by prefixing '\r'. The console used to read its pipes with universal
-newline translation, which rewrote every '\r' to '\n' — so each progress refresh looked like a
finished line and got appended. A 4-hour run left ~30k near-identical rows in a Text widget that
was never trimmed, and the console became the slowest thing in the app.

This covers both halves: '\r' collapses onto one row, and the widget is bounded. The last case
runs a REAL subprocess through run_subprocess with a REAL tqdm, because the bug lived in the
pipe configuration — a unit test that hand-feeds strings would have passed throughout.

Run: venv/Scripts/python.exe tests/test_console_stream.py
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

# The console carries UI glyphs and tqdm block characters; Windows stdout defaults to cp1252
# and raises on them, which would kill the run while merely REPORTING a passing assertion.
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


def reset():
    gui.console_output.configure(state="normal")
    gui.console_output.delete("1.0", "end")
    gui.console_output.configure(state="disabled")
    gui._console_transient = False
    gui._log_buffer = []
    gui._log_buf_transient = False


def body():
    return gui.console_output.get("1.0", "end-1c")


def rows():
    return int(gui.console_output.index("end-1c").split(".")[0])


# --- 1. carriage-return refreshes collapse onto a single row ------------------------------
reset()
for pct in range(0, 101, 10):
    gui.update_console(f"\rsteps: {pct}%")       # tqdm's shape: LEADING '\r'
ck("11 progress refreshes -> 1 console row", rows() == 1, f"{rows()} rows")
ck("  the row shows only the latest refresh", body() == "steps: 100%", repr(body()))
ck("  no literal control character rendered", "\r" not in body())

# --- 2. a finished line supersedes the live progress row ----------------------------------
gui.update_console("Epoch 1 complete\n")
ck("finished line replaces the transient row", body() == "Epoch 1 complete\n", repr(body()))
gui.update_console("\rsteps: 5%")
gui.update_console("\rsteps: 6%")
ck("progress resumes below a finished line",
   body() == "Epoch 1 complete\nsteps: 6%", repr(body()))
gui.update_console("done\n")
ck("  and finalises without stranding the bar",
   body() == "Epoch 1 complete\ndone\n", repr(body()))

# --- 3. CRLF is a FINISHED line, not a progress refresh -----------------------------------
reset()
gui.update_console("windows line\r\n")
gui.update_console("second\r\n")
ck("CRLF treated as two finished lines", body() == "windows line\nsecond\n", repr(body()))

# --- 4. the widget is actually bounded -----------------------------------------------------
reset()
for i in range(12000):
    gui.update_console(f"line {i}\n")
n = rows()
ck("12k lines stay under the cap", n <= gui._CONSOLE_MAX_LINES,
   f"{n} rows (cap {gui._CONSOLE_MAX_LINES})")
ck("  the newest line survives the trim", body().rstrip("\n").endswith("line 11999"))
ck("  the oldest line was trimmed away", "line 0\n" not in body())

# --- 5. the global log buffer doesn't hoard progress lines either --------------------------
reset()
for pct in range(0, 101, 5):
    gui.update_console(f"\rcaching {pct}%")
ck("21 refreshes -> 1 buffer entry", len(gui._log_buffer) == 1, f"{len(gui._log_buffer)}")

# --- 6. end to end: a real subprocess, a real tqdm, the real pipe wiring -------------------
# This is the case that matters. The defect was in Popen's newline handling, so it only
# reproduces through an actual pipe — steps 1-5 above passed before the fix as well.
fake = os.path.join(tempfile.gettempdir(), "fizgig_fake_trainer.py")
with open(fake, "w", encoding="utf-8") as fh:
    fh.write(
        "import sys, time\n"
        "from tqdm import tqdm\n"
        "print('loading model...', flush=True)\n"
        "for _ in tqdm(range(60), desc='steps', file=sys.stderr, mininterval=0):\n"
        "    time.sleep(0.005)\n"
        "print('saving checkpoint', flush=True)\n"
    )

reset()
ticks = [0]


def poll():
    ticks[0] += 1
    if "process completed" in body() or ticks[0] > 600:
        root.after(400, root.quit)        # let trailing after() callbacks drain
        return
    root.after(50, poll)


root.after(50, poll)
# A real mainloop, not update() polling: run_subprocess marshals from worker threads via
# after(), which raises "main thread is not in main loop" outside one.
root.after(0, lambda: gui.run_subprocess([sys.executable, fake], "fake training"))
root.mainloop()

out = body()
progress_rows = [r for r in out.split("\n") if r.lstrip().startswith("steps:")]
ck("real tqdm collapses to at most one progress row",
   len(progress_rows) <= 1, f"{len(progress_rows)} progress rows")
ck("  the surviving bar reached 100%",
   bool(progress_rows) and "100%" in progress_rows[0], progress_rows[:1])
ck("  no literal carriage returns reached the widget", "\r" not in out)
ck("  ordinary stdout lines all survived",
   all(s in out for s in ("loading model...", "saving checkpoint")))
ck("  a 60-step run fits in a handful of rows",
   len(out.split("\n")) < 12, f"{len(out.split('\n'))} rows")

try:
    os.remove(fake)
except Exception:
    pass

# --- 7. output is marshalled to Tk in batches, not one callback per line -------------------
# Each update_console does a yview(), an insert, a see() and two configure()s. One after(0) per
# line meant a burst (latent caching over a few hundred images) queued thousands of separate
# callbacks and the window sat unresponsive draining them. Counting real after() calls against
# real lines is the only way to see this — the console CONTENT is identical either way.
burst = os.path.join(tempfile.gettempdir(), "fizgig_burst.py")
N_LINES = 4000
with open(burst, "w", encoding="utf-8") as fh:
    fh.write(f"for i in range({N_LINES}): print('burst line %d' % i, flush=False)\n")

reset()
after_calls = [0]
real_after = root.after


def counting_after(*a, **k):
    after_calls[0] += 1
    return real_after(*a, **k)


root.after = counting_after
ticks = [0]


def poll2():
    ticks[0] += 1
    if "process completed" in body() or ticks[0] > 900:
        real_after(600, root.quit)
        return
    real_after(50, poll2)


real_after(50, poll2)
real_after(0, lambda: gui.run_subprocess([sys.executable, burst], "burst"))
root.mainloop()
root.after = real_after

got = body().count("burst line")
ck(f"all {N_LINES} burst lines reached the console (after trim)", got > 0, f"{got} visible")
ck("  marshalling used far fewer callbacks than lines",
   after_calls[0] < N_LINES / 10, f"{after_calls[0]} after() calls for {N_LINES} lines")

try:
    os.remove(burst)
except Exception:
    pass

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

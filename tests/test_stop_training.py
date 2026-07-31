"""Stop Training: the kill runs off the Tk thread, except when closing the app.

stop_training() used to do all its waiting inline on the Tk main thread — a taskkill, then
wait(timeout=5), then an UNBOUNDED wait() after kill(), then a thread join. Tearing down a run
holding 14+ GB of VRAM takes seconds, so the window stopped repainting and Windows greyed it
out as "not responding" at exactly the moment the user is anxious about whether Stop worked.

It is now threaded by default. The app-close path passes wait=True, because there the window is
about to be destroyed and returning early would orphan the training process — the precise thing
_on_app_close exists to prevent. Both halves are checked here.

Run: venv/Scripts/python.exe tests/test_stop_training.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

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

SLEEPER = [sys.executable, "-c", "import time; time.sleep(300)"]
real_terminate = gui._terminate_training_process


def spawn():
    p = subprocess.Popen(SLEEPER, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gui.current_process = p
    gui._stop_in_flight = False
    return p


def slow_terminate(delay):
    """A teardown that takes `delay` seconds — stands in for a real VRAM-heavy exit."""
    def _t(proc):
        time.sleep(delay)
        real_terminate(proc)
    return _t


# --- 1. nothing running ---------------------------------------------------------------------
gui.current_process = None
gui.stop_training()
ck("stopping with no process is a quiet no-op", gui.current_process is None)

# --- 2. the default path returns immediately and kills in the background --------------------
proc = spawn()
gui._terminate_training_process = slow_terminate(2.0)
t0 = time.time()
gui.stop_training()                       # what the Stop button calls (command=, no args)
elapsed = time.time() - t0
ck("Stop returns without waiting for the kill", elapsed < 0.5, f"{elapsed:.2f}s")
ck("  the child is still being torn down", proc.poll() is None)
ck("  and the run is marked in-flight", gui._stop_in_flight is True)

# --- 3. a second click while the kill is in flight is ignored -------------------------------
calls = []
gui._terminate_training_process = lambda p: (calls.append(p), time.sleep(1.0), real_terminate(p))
gui.stop_training()                       # must be swallowed by the re-entrancy guard
ck("a second Stop during teardown is ignored", calls == [], f"{len(calls)} extra kills")

# the background kill from step 2 should complete on its own
deadline = time.time() + 20
while time.time() < deadline and proc.poll() is None:
    time.sleep(0.05)
ck("  the background kill still finished the job", proc.poll() is not None, proc.poll())
deadline = time.time() + 10
while time.time() < deadline and gui._stop_in_flight:
    time.sleep(0.05)
ck("  and the in-flight flag cleared", gui._stop_in_flight is False)

# --- 4. wait=True blocks until the child is reaped (the app-close contract) -----------------
proc = spawn()
gui._terminate_training_process = slow_terminate(1.5)
t0 = time.time()
gui.stop_training(wait=True)
elapsed = time.time() - t0
ck("wait=True blocks until teardown completes", elapsed >= 1.4, f"{elapsed:.2f}s")
ck("  the child is dead when it returns", proc.poll() is not None, proc.poll())

# --- 5. the real terminator actually kills the tree -----------------------------------------
gui._terminate_training_process = real_terminate
proc = spawn()
gui.stop_training(wait=True)
ck("the real kill path reaps the process", proc.poll() is not None, proc.poll())
ck("  and clears the in-flight flag", gui._stop_in_flight is False)

# --- 6. the close handler uses the blocking form --------------------------------------------
# Checked structurally: an async stop here would let the process outlive master.destroy().
import inspect  # noqa: E402
src = inspect.getsource(gui._on_app_close)
ck("_on_app_close calls stop_training(wait=True)", "stop_training(wait=True)" in src)

sig = inspect.signature(G.LoRATrainerGUI.stop_training)
ck("stop_training defaults to the non-blocking form",
   sig.parameters["wait"].default is False, sig)

for p in (proc,):
    try:
        p.kill()
    except Exception:
        pass

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

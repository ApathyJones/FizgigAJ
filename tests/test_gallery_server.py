"""The samples gallery HTTP server: socket lifecycle and concurrency.

Two defects this pins down:

  * stop_gallery_server() called shutdown() but never server_close(). shutdown() only stops the
    serve_forever loop — the LISTENING SOCKET stays open. The gallery restarts with every
    training run, so that leaked one descriptor (and held one port) per run for the life of the
    session.
  * The server was a plain HTTPServer, which handles one request at a time. A gallery page
    holding a few dozen sample PNGs fetched them strictly in series, and the /set_baselines
    POST (which starts CPU likeness scoring) blocked every image load while it ran.

Run: venv/Scripts/python.exe tests/test_gallery_server.py
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

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

out_dir = tempfile.mkdtemp(prefix="fizgig_gallery_")
gui.settings["LORA_OUTPUT_DIR"] = out_dir
samples = os.path.join(out_dir, "sample")
os.makedirs(samples, exist_ok=True)
with open(os.path.join(samples, "probe.txt"), "w", encoding="utf-8") as fh:
    fh.write("hello")


def listening_ports():
    """Loopback sockets this process holds open in LISTEN state."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return {c.laddr.port for c in psutil.Process().net_connections(kind="tcp")
                if c.status == "LISTEN"}
    except Exception:
        return None


# --- 1. the server binds, serves, and reports the port the OS actually gave it ------------
gui.start_gallery_server()
port = gui.gallery_server_port
ck("server started and reported a port", bool(port), port)
ck("  it is the port actually bound",
   port == gui.gallery_server.server_address[1], gui.gallery_server.server_address)
body = urllib.request.urlopen(f"http://127.0.0.1:{port}/probe.txt", timeout=10).read()
ck("  and it serves the samples directory", body == b"hello", body)

# --- 2. concurrency: a slow request must not block a second one --------------------------
# The handler is exercised through a real socket, so this measures the server class, not a mock.
started = threading.Event()
release = threading.Event()
orig_translate = gui.gallery_server.RequestHandlerClass.translate_path


def slow_translate(handler_self, path):
    if path.startswith("/probe.txt"):
        started.set()
        release.wait(timeout=10)          # hold this handler open
    return orig_translate(handler_self, path)


gui.gallery_server.RequestHandlerClass.translate_path = slow_translate
slow_done = []
threading.Thread(
    target=lambda: slow_done.append(
        urllib.request.urlopen(f"http://127.0.0.1:{port}/probe.txt", timeout=15).read()),
    daemon=True).start()
ck("a slow request is in flight", started.wait(timeout=10))
t0 = time.time()
try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/nope.txt", timeout=5)
    second_ok = True
except urllib.error.HTTPError:
    second_ok = True                      # a 404 still proves the server answered
except Exception:
    second_ok = False
elapsed = time.time() - t0
release.set()
ck("  a second request is served while the first is blocked", second_ok)
ck("  and it did not wait behind it", elapsed < 3.0, f"{elapsed:.2f}s")
gui.gallery_server.RequestHandlerClass.translate_path = orig_translate

# --- 3. stop closes the listening socket EXPLICITLY, not eventually -----------------------
# Holding our own reference is the whole point of this check. Without server_close(), the fd
# survives exactly as long as some reference does — CPython's socket finalizer closes it once
# the last one drops, which is why the old code never showed an observable descriptor leak.
# What it did do was defer the close to garbage collection (with a ResourceWarning) instead of
# releasing it deterministically at stop. This pins the explicit contract.
srv = gui.gallery_server
sock = srv.socket
gui.stop_gallery_server()
ck("stop clears the server handle", gui.gallery_server is None and gui.gallery_server_port is None)
ck("  the listening socket is closed by the time stop returns",
   sock.fileno() == -1, f"fileno={sock.fileno()}")
del srv, sock

# --- 4. many start/stop cycles stay flat --------------------------------------------------
base = listening_ports()
for _ in range(15):
    gui.start_gallery_server()
    gui.stop_gallery_server()
end = listening_ports()
if base is None or end is None:
    print("SKIP  listener-count check (psutil unavailable)")
else:
    ck("15 start/stop cycles accumulate no listeners", len(end) <= len(base),
       f"{len(base)} -> {len(end)} listeners")

# --- 5. stop is idempotent ------------------------------------------------------------------
gui.stop_gallery_server()
ck("stopping an already-stopped server is a no-op", gui.gallery_server is None)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)

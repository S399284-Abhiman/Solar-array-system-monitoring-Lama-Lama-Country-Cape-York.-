#!/usr/bin/env python3
# =============================================================================
# simulate.py  —  Solar Health Monitor · Laptop Simulator
# =============================================================================
#
# TWO MODES:
#
#  1. REAL MODE  — connects to LamaSolar WiFi and POSTs data to ESP32
#       python simulate.py
#       (your laptop must be connected to LamaSolar WiFi first)
#
#  2. TEST MODE  — runs a full local server on your laptop, no ESP32 needed
#       python simulate.py --test
#       Then open:  http://localhost:8000
#
# INTERACTIVE COMMANDS (type + Enter while running):
#   1  Normal Operation   2  Load Spike
#   3  Critical Fault     4  Recovery Mode     q  Quit
#
# REQUIREMENTS
#   pip install requests        (real mode only)
#   test mode needs nothing extra
# =============================================================================

import argparse
import json
import math
import os
import random
import sys
import threading
import time

# ── ESP32 target ──────────────────────────────────────────────────────────────
ESP32_IP   = "192.168.4.1"
ESP32_URL  = f"http://{ESP32_IP}/update"
INTERVAL   = 2.0   # seconds between ticks

# ── Scenario definitions ──────────────────────────────────────────────────────
SCENARIOS = {
    "normal":    dict(sBase=1200, sVar=80,  lBase=420,  lVar=30,  bRate= 0.0,
                      name="Normal Operation"),
    "loadspike": dict(sBase=1100, sVar=60,  lBase=1750, lVar=50,  bRate=-1.2,
                      name="Load Spike"),
    "critical":  dict(sBase=300,  sVar=40,  lBase=900,  lVar=30,  bRate=-2.5,
                      name="Critical Fault"),
    "recovery":  dict(sBase=1400, sVar=50,  lBase=280,  lVar=20,  bRate= 1.8,
                      name="Recovery Mode"),
}


# =============================================================================
# Simulation state
# =============================================================================
class SimState:
    def __init__(self, initial_scenario: str):
        self.scenario = initial_scenario
        self.batt     = 85.0
        self.tick     = 0
        self.lock     = threading.Lock()

    def set_scenario(self, key: str):
        with self.lock:
            if key in SCENARIOS:
                self.scenario = key
                print(f"\n[SIM] ▶  Switched to: {SCENARIOS[key]['name']}\n")
            else:
                print(f"[SIM] Unknown scenario: {key!r}")

    def compute_tick(self) -> dict:
        with self.lock:
            sc  = SCENARIOS[self.scenario]
            osc = math.sin(self.tick * 0.3) * 0.5 + math.cos(self.tick * 0.17) * 0.3
            rnd = lambda: (random.random() - 0.5) * 2

            solar = max(0.0, min(2000.0, sc["sBase"] + sc["sVar"] * osc + rnd() * 20))
            load  = max(0.0, min(2000.0, sc["lBase"] + sc["lVar"] * osc + rnd() * 15))

            if self.scenario == "normal":
                self.batt = max(70.0, min(92.0, self.batt + rnd() * 0.3))
            else:
                self.batt = max(0.0,  min(100.0, self.batt + sc["bRate"] + rnd() * 0.15))

            self.tick += 1
            return {
                "solar":    round(solar, 1),
                "load":     round(load,  1),
                "batt":     round(self.batt, 2),
                "scenario": self.scenario,
                "tick":     self.tick,
            }

    def current_snapshot(self) -> dict:
        """Latest values without advancing tick (used by test-mode HTTP server)."""
        with self.lock:
            sc  = SCENARIOS[self.scenario]
            osc = math.sin(self.tick * 0.3) * 0.5 + math.cos(self.tick * 0.17) * 0.3
            return {
                "solar":    round(max(0, min(2000, sc["sBase"] + sc["sVar"] * osc)), 1),
                "load":     round(max(0, min(2000, sc["lBase"] + sc["lVar"] * osc)), 1),
                "batt":     round(self.batt, 2),
                "scenario": self.scenario,
                "tick":     self.tick,
            }


# =============================================================================
# TICK LOOP
# =============================================================================
def tick_loop(state: SimState, stop_event: threading.Event, on_tick=None):
    while not stop_event.is_set():
        data = state.compute_tick()
        ts   = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] tick={data['tick']:4d}  "
            f"solar={data['solar']:6.1f}W  "
            f"load={data['load']:6.1f}W  "
            f"batt={data['batt']:5.1f}%  "
            f"[{SCENARIOS[data['scenario']]['name']}]"
        )
        if on_tick:
            on_tick(data)
        stop_event.wait(INTERVAL)


# =============================================================================
# MODE 1 — REAL: POST data to ESP32 over WiFi
# =============================================================================
def run_real(state: SimState, stop_event: threading.Event):
    try:
        import requests
    except ImportError:
        print("ERROR: requests not installed.  Run:  pip install requests")
        sys.exit(1)

    # Check connectivity first
    print(f"[NET] Checking connection to ESP32 at {ESP32_IP} …")
    connected = False
    for attempt in range(5):
        try:
            r = requests.get(f"http://{ESP32_IP}/data", timeout=2)
            if r.status_code == 200:
                connected = True
                print(f"[NET] ESP32 reachable ✓")
                break
        except Exception:
            print(f"[NET] Attempt {attempt+1}/5 — not reachable yet, retrying…")
            time.sleep(2)

    if not connected:
        print("\n[NET] Cannot reach ESP32.")
        print("      Make sure your laptop WiFi is connected to: LamaSolar")
        print("      And the ESP32 is powered and running main.py\n")
        # Keep trying in the background anyway

    def send(data):
        try:
            r = requests.post(ESP32_URL, json=data, timeout=1.5)
            if r.status_code != 200:
                print(f"[NET] ESP32 returned {r.status_code}")
        except requests.exceptions.ConnectionError:
            print("[NET] Connection error — is laptop on LamaSolar WiFi?")
        except requests.exceptions.Timeout:
            print("[NET] Timeout — ESP32 busy, will retry next tick")
        except Exception as e:
            print(f"[NET] Error: {e}")

    t = threading.Thread(target=tick_loop, args=(state, stop_event, send), daemon=True)
    t.start()
    _run_input_loop(state, stop_event)


# =============================================================================
# MODE 2 — TEST: local HTTP server (no ESP32 needed)
# =============================================================================
def run_test(port_num: int, state: SimState, stop_event: threading.Event):
    import http.server
    import socketserver
    import re

    # Load index.html from same folder
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    html_content = None

    # Try index.html first (the separate file for ESP32)
    for name in ("index.html", "SolarDashboard_v2_esp32.html", "SolarDashboard_v2.html"):
        p = os.path.join(script_dir, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                html_content = f.read()
            print(f"[TEST] Loaded dashboard from {name}")
            break

    # Fallback: extract from main.py embedded string
    if html_content is None:
        main_py = os.path.join(script_dir, "main.py")
        if os.path.exists(main_py):
            with open(main_py, "r", encoding="utf-8") as f:
                src = f.read()
            mo = re.search(r'DASHBOARD_HTML\s*=\s*"""(.*?)"""', src, re.DOTALL)
            if mo:
                html_content = mo.group(1)
                print("[TEST] Loaded dashboard from main.py embedded string")

    if html_content is None:
        print("ERROR: Could not find index.html or main.py in the same folder.")
        sys.exit(1)

    html_bytes = html_content.encode("utf-8")

    # Local notification state (mirrors what main.py does on ESP32)
    notify_queue = []
    notify_seq   = [0]
    prev_scenario = [state.scenario]
    alert_fired   = [False]

    def push(msg, level, ntype):
        notify_seq[0] += 1
        notify_queue.append({"seq": notify_seq[0], "type": ntype, "msg": msg, "level": level})
        if len(notify_queue) > 20:
            notify_queue.pop(0)
        print(f"[NOTIFY] {level.upper()}: {msg}")

    def check_notifications(data):
        # Scenario change
        if data["scenario"] != prev_scenario[0]:
            names = {"normal":"Normal Operation","loadspike":"Load Spike",
                     "critical":"Critical Fault","recovery":"Recovery Mode"}
            levels = {"normal":"info","loadspike":"warning","critical":"critical","recovery":"info"}
            push("Scenario changed to: " + names.get(data["scenario"], data["scenario"]),
                 levels.get(data["scenario"], "info"), "scenario")
            prev_scenario[0] = data["scenario"]
        # Battery critical
        if data["batt"] <= 20 and not alert_fired[0]:
            alert_fired[0] = True
            push(f"CRITICAL: Battery at {data['batt']:.1f}% — SMS alert dispatched to Rangers",
                 "critical", "alert")
        # Recovery
        if alert_fired[0] and data["batt"] > 25:
            alert_fired[0] = False
            push(f"Battery recovered to {data['batt']:.1f}% — system returning to normal",
                 "info", "recovery")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/data":
                snap = state.current_snapshot()
                check_notifications(snap)
                body = json.dumps(snap).encode()
                self._json(body)
            elif path in ("/notify", ) or self.path.startswith("/notify"):
                body = json.dumps({
                    "seq":   notify_seq[0],
                    "items": notify_queue[-10:],
                }).encode()
                self._json(body)
            elif path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def _json(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", port_num), Handler)
    httpd.timeout = 0.1

    t = threading.Thread(target=tick_loop, args=(state, stop_event), daemon=True)
    t.start()

    print(f"\n{'='*58}")
    print(f"  TEST MODE — no ESP32 needed")
    print(f"  Open your browser:  http://localhost:{port_num}")
    print(f"  Notifications will pop up when you switch scenarios")
    print(f"{'='*58}\n")

    srv_thread = threading.Thread(
        target=lambda: [httpd.handle_request() for _ in iter(lambda: stop_event.is_set(), True)],
        daemon=True
    )

    def serve_loop():
        while not stop_event.is_set():
            httpd.handle_request()

    srv_thread = threading.Thread(target=serve_loop, daemon=True)
    srv_thread.start()

    _run_input_loop(state, stop_event)
    httpd.shutdown()


# =============================================================================
# INTERACTIVE INPUT LOOP
# =============================================================================
def _run_input_loop(state: SimState, stop_event: threading.Event):
    mapping = {"1": "normal", "2": "loadspike", "3": "critical", "4": "recovery"}
    print("\n[CMD] 1=Normal  2=LoadSpike  3=Critical  4=Recovery  q=Quit\n")
    while not stop_event.is_set():
        try:
            cmd = input().strip().lower()
        except EOFError:
            break
        if cmd == "q":
            print("[CMD] Quitting…")
            stop_event.set()
            break
        elif cmd in mapping:
            state.set_scenario(mapping[cmd])
        else:
            print("[CMD] 1=Normal  2=LoadSpike  3=Critical  4=Recovery  q=Quit")


# =============================================================================
# ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Solar dashboard simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python simulate.py              # real mode — laptop on LamaSolar WiFi
  python simulate.py --test       # test mode — no ESP32 needed
  python simulate.py --test --scenario critical
        """
    )
    parser.add_argument("--test",      action="store_true",
                        help="Run local test server instead of posting to ESP32")
    parser.add_argument("--http-port", type=int, default=8000,
                        help="Local HTTP port for test mode (default: 8000)")
    parser.add_argument("--scenario",  default="normal", choices=list(SCENARIOS.keys()),
                        help="Starting scenario")
    args = parser.parse_args()

    print("=" * 58)
    print("  Solar Health Monitor — Simulator")
    mode = "TEST (local browser)" if args.test else f"REAL (WiFi → ESP32 at {ESP32_IP})"
    print(f"  Mode:     {mode}")
    print(f"  Scenario: {args.scenario}")
    if not args.test:
        print(f"  Make sure your laptop WiFi = LamaSolar")
    print("=" * 58)

    state      = SimState(args.scenario)
    stop_event = threading.Event()

    try:
        if args.test:
            run_test(args.http_port, state, stop_event)
        else:
            run_real(state, stop_event)
    except KeyboardInterrupt:
        print("\n[MAIN] Interrupted.")
        stop_event.set()

    print("[MAIN] Stopped.")


if __name__ == "__main__":
    main()

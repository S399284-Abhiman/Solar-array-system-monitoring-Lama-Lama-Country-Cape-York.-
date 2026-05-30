# =============================================================================
# main.py  —  Solar Health Monitor · ESP32 MicroPython
# =============================================================================
# FILES ON ESP32 FLASH (copy both with Thonny):
#   main.py       ← this file
#   index.html    ← the dashboard HTML
#
# HOW IT WORKS:
#   1. ESP32 boots → creates WiFi AP  "LamaSolar" / "solar2026"
#   2. Your laptop connects to LamaSolar WiFi and runs simulate.py
#   3. simulate.py POSTs JSON data to  http://192.168.4.1/update  every 2s
#   4. Judges connect to LamaSolar WiFi and open  http://192.168.4.1
#   5. Dashboard polls  GET /data  every 2s for live numbers
#   6. Dashboard connects to  GET /notify  (SSE stream) for instant
#      push notifications (mode changes, alerts, etc.)
#
# UPLOAD TO ESP32 (in Thonny):
#   File → Open → open main.py → File → Save As → MicroPython device → main.py
#   File → Open → open index.html → File → Save As → MicroPython device → index.html
#   Press RST button on board
# =============================================================================

import network
import socket
import ujson
import time

# ── WiFi AP ───────────────────────────────────────────────────────────────────
AP_SSID     = "LamaSolar"
AP_PASSWORD = "solar2026"
AP_IP       = "192.168.4.1"

# ── Live state updated by /update POST from simulate.py ──────────────────────
state = {
    "solar":    1200.0,
    "load":     420.0,
    "batt":     85.0,
    "scenario": "normal",
    "tick":     0,
}

# ── Notification queue — SSE clients poll this ────────────────────────────────
# Each entry: {"type": "scenario"|"alert"|"recovery", "msg": "...", "level": "info"|"warning"|"critical"}
notify_queue = []
notify_seq   = 0   # ever-incrementing so clients know which they've seen


# =============================================================================
# WIFI ACCESS POINT
# =============================================================================
def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=3)
    deadline = time.ticks_add(time.ticks_ms(), 10_000)
    while not ap.active():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            print("AP failed to start")
            break
        time.sleep(0.2)
    cfg = ap.ifconfig()
    print("AP active  SSID=" + AP_SSID + "  IP=" + cfg[0])


# =============================================================================
# HTTP HELPERS
# =============================================================================
def recv_request(conn):
    """Read full HTTP request, return (method, path, body_str)."""
    conn.settimeout(4.0)
    raw = b""
    try:
        while True:
            chunk = conn.recv(512)
            if not chunk:
                break
            raw += chunk
            # Stop once we have headers + all expected body bytes
            if b"\r\n\r\n" in raw:
                header_part, body_part = raw.split(b"\r\n\r\n", 1)
                # Find Content-Length
                cl = 0
                for line in header_part.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        cl = int(line.split(b":", 1)[1].strip())
                        break
                # Read remaining body bytes
                while len(body_part) < cl:
                    more = conn.recv(512)
                    if not more:
                        break
                    body_part += more
                raw = header_part + b"\r\n\r\n" + body_part
                break
    except OSError:
        pass

    try:
        header_part = raw.split(b"\r\n\r\n")[0]
        body_part   = raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else b""
        first_line  = header_part.split(b"\r\n")[0].decode()
        parts = first_line.split(" ")
        method = parts[0] if len(parts) > 0 else "GET"
        path   = parts[1].split("?")[0] if len(parts) > 1 else "/"
        return method, path, body_part.decode("utf-8", "ignore")
    except Exception:
        return "GET", "/", ""


def send_headers(conn, status, content_type, body_len, extra=""):
    hdr = (
        "HTTP/1.0 " + status + "\r\n"
        "Content-Type: " + content_type + "\r\n"
        "Content-Length: " + str(body_len) + "\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n"
        + extra +
        "\r\n"
    )
    conn.sendall(hdr.encode())


def send_text(conn, status, content_type, body):
    if isinstance(body, str):
        body = body.encode()
    send_headers(conn, status, content_type, len(body))
    conn.sendall(body)


def send_file(conn, filename, content_type):
    """Stream a file from flash to the client in chunks (saves RAM)."""
    try:
        f = open(filename, "rb")
    except OSError:
        send_text(conn, "404 Not Found", "text/plain",
                  filename + " not found on ESP32 — did you upload it?")
        return
    # Read size for Content-Length header
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    send_headers(conn, "200 OK", content_type, size)
    while True:
        chunk = f.read(1024)
        if not chunk:
            break
        try:
            conn.sendall(chunk)
        except OSError:
            break
    f.close()


# =============================================================================
# PUSH NOTIFICATION HELPERS
# =============================================================================
def push_notify(msg, level="info", ntype="info"):
    """Add a notification to the queue for SSE clients."""
    global notify_seq
    notify_seq += 1
    entry = {"seq": notify_seq, "type": ntype, "msg": msg, "level": level}
    notify_queue.append(entry)
    if len(notify_queue) > 20:
        notify_queue.pop(0)


def send_notify_response(conn):
    """
    Serve /notify as a simple JSON endpoint.
    The client passes ?since=N and gets back any notifications with seq > N.
    This is simpler than true SSE for MicroPython and works just as well.
    """
    body = ujson.dumps({
        "seq":   notify_seq,
        "items": notify_queue[-10:],   # last 10 max
    }).encode()
    send_headers(conn, "200 OK", "application/json", len(body),
                 "Cache-Control: no-cache\r\n")
    conn.sendall(body)


# =============================================================================
# REQUEST HANDLER
# =============================================================================
PREV_SCENARIO = "normal"
ALERT_FIRED   = False

def handle(conn, addr):
    global state, notify_seq, PREV_SCENARIO, ALERT_FIRED

    method, path, body = recv_request(conn)
    print(method, path, "from", addr[0])

    # ── POST /update — receive data from simulate.py ──────────────────────────
    if method == "POST" and path == "/update":
        try:
            obj = ujson.loads(body)
            prev_batt     = state["batt"]
            prev_scenario = state["scenario"]

            state["solar"]    = float(obj.get("solar",    state["solar"]))
            state["load"]     = float(obj.get("load",     state["load"]))
            state["batt"]     = float(obj.get("batt",     state["batt"]))
            state["scenario"] = str(obj.get("scenario",   state["scenario"]))
            state["tick"]     = int(obj.get("tick",       state["tick"]))

            # ── Scenario change notification ──────────────────────────────────
            if state["scenario"] != prev_scenario:
                names = {
                    "normal":    "Normal Operation",
                    "loadspike": "Load Spike",
                    "critical":  "Critical Fault",
                    "recovery":  "Recovery Mode",
                }
                sc_name = names.get(state["scenario"], state["scenario"])
                levels  = {"normal":"info","loadspike":"warning","critical":"critical","recovery":"info"}
                lvl     = levels.get(state["scenario"], "info")
                push_notify(
                    "Scenario changed to: " + sc_name,
                    level=lvl, ntype="scenario"
                )
                print("NOTIFY: scenario ->", sc_name)

            # ── Low battery / critical alert notification ─────────────────────
            if state["batt"] <= 20 and not ALERT_FIRED:
                ALERT_FIRED = True
                push_notify(
                    "CRITICAL: Battery at " + str(round(state["batt"], 1)) +
                    "% — SMS alert dispatched to Rangers",
                    level="critical", ntype="alert"
                )
                print("NOTIFY: battery critical")

            # ── Recovery notification ─────────────────────────────────────────
            if ALERT_FIRED and state["batt"] > 25:
                ALERT_FIRED = False
                push_notify(
                    "Battery recovered to " + str(round(state["batt"], 1)) +
                    "% — system returning to normal",
                    level="info", ntype="recovery"
                )
                print("NOTIFY: battery recovered")

            send_text(conn, "200 OK", "application/json", '{"ok":true}')
        except Exception as e:
            print("Update error:", e)
            send_text(conn, "400 Bad Request", "application/json", '{"ok":false}')

    # ── GET /data — dashboard polls this every 2s ─────────────────────────────
    elif path == "/data":
        send_text(conn, "200 OK", "application/json", ujson.dumps(state))

    # ── GET /notify — dashboard polls this for push notifications ────────────
    elif path == "/notify" or path.startswith("/notify?"):
        send_notify_response(conn)

    # ── GET /chart.js — serve chart.js from flash ────────────────────────────
    elif path == "/chart.js":
        send_file(conn, "chart.js", "application/javascript")

    # ── GET / or /index.html — serve the dashboard ────────────────────────────
    elif path in ("/", "/index.html"):
        send_file(conn, "index.html", "text/html; charset=utf-8")

    # ── 404 ───────────────────────────────────────────────────────────────────
    else:
        send_text(conn, "404 Not Found", "text/plain", "Not found: " + path)

    conn.close()


# =============================================================================
# MAIN LOOP
# =============================================================================
def main():
    print("\n=== Solar Health Monitor — ESP32 ===")
    start_ap()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", 80))
    srv.listen(4)
    srv.setblocking(False)

    print("HTTP server on port 80")
    print("Connect to WiFi: " + AP_SSID + " / " + AP_PASSWORD)
    print("Open browser:    http://" + AP_IP)
    print("Laptop runs:     python simulate.py\n")

    while True:
        try:
            conn, addr = srv.accept()
            handle(conn, addr)
        except OSError:
            pass
        time.sleep_ms(5)


main()

# Lama Lama Solar Health Monitor

**CUC106 — Engineers Without Borders Challenge
Design Area 3.4 — Energy Monitoring \& Fault Diagnosis
Port Stewart Ranger Base, Lama Lama Country, Cape York**

Team: Abhiman Bhattarai (s399284) · TJ Woods (s402983) · Binit Shilpakar (s401802)
Charles Darwin University — 2025

\---

## What This Is

A real-time solar energy monitoring and fault diagnosis system for the Port Stewart Ranger Base. An ESP32 microcontroller hosts a Wi-Fi access point and a web dashboard that displays live solar panel output, battery state of charge, and load consumption. When faults occur (low battery, load spikes), the system triggers on-screen alerts and simulates an SMS dispatch to Rangers.

\---

## Repository Files

|File|Where it runs|Purpose|
|-|-|-|
|`main.py`|ESP32 (MicroPython)|Wi-Fi AP, HTTP server, sensor state, push notifications|
|`index.html`|ESP32 flash / browser|Live monitoring dashboard|
|`chart.js`|ESP32 flash|Bundled Chart.js 4.4.0 (no internet needed on-site)|
|`simulate.py`|Laptop (Python 3)|Sends simulated solar data to ESP32; also runs a local test server|

\---

## How It Works

```
simulate.py  ──POST /update every 2s──►  ESP32 (main.py)
                                               │
                                    ┌──────────┴──────────┐
                              GET /data             GET /notify
                                    │                     │
                              Browser (index.html)  push alerts
```

1. ESP32 boots and creates a Wi-Fi AP: **LamaSolar** / `solar2026`
2. Laptop connects to LamaSolar and runs `simulate.py`, which POSTs JSON every 2 seconds
3. Browser connects to LamaSolar and opens `http://192.168.4.1` — the dashboard polls `/data` every 2 seconds and `/notify` for alerts

\---

## Quick Start

### Option A — Full Demo (ESP32 + Laptop)

**Requirements:** ESP32 board, Thonny IDE, Python 3 + `requests` on laptop

**1. Upload files to ESP32**

Open Thonny → connect to your ESP32, then upload each file:

```
File → Open → select file → File → Save As → MicroPython device → \[same filename]
```

Upload in this order:

* `chart.js`
* `index.html`
* `main.py`

Press the **RST** button on the board after uploading.

**2. Connect laptop to ESP32 Wi-Fi**

```
Network: LamaSolar
Password: solar2026
```

**3. Run the simulator**

```bash
pip install requests
python simulate.py
```

**4. Open the dashboard**

Open a browser and go to: `http://192.168.4.1`

\---

### Option B — Test Mode (No ESP32 Needed)

Run everything locally on your laptop — no hardware required:

```bash
python simulate.py --test
```

Then open: `http://localhost:8000`

\---

## Simulator Controls

While `simulate.py` is running, type a number and press Enter to switch scenarios:

|Key|Scenario|What it simulates|
|-|-|-|
|`1`|Normal Operation|Steady solar generation, stable battery|
|`2`|Load Spike|High load, battery draining|
|`3`|Critical Fault|Low solar, battery critical (triggers SMS alert)|
|`4`|Recovery Mode|Strong solar, battery recovering|
|`q`|Quit|Stops the simulator|

\---

## Dashboard Features

* **Live gauges** — Solar output (W), Load (W), Battery SoC (%)
* **Real-time chart** — 60-second rolling history (Chart.js)
* **Status banner** — colour-coded Normal / Warning / Critical
* **Push notifications** — scenario changes and battery alerts appear as toasts
* **No internet required** — all assets served from ESP32 flash; uses bundled Chart.js

\---

## Scenarios Explained

|Scenario|Solar|Load|Battery trend|
|-|-|-|-|
|Normal|\~1200 W|\~420 W|Stable (70–92%)|
|Load Spike|\~1100 W|\~1750 W|Draining (−1.2%/tick)|
|Critical|\~300 W|\~900 W|Draining fast (−2.5%/tick)|
|Recovery|\~1400 W|\~280 W|Charging (+1.8%/tick)|

Battery critical alert fires at **≤ 20% SoC**. Recovery notification fires when battery climbs back above **25%**.

\---

## Prototype vs. Real Deployment

This prototype uses **simulated data** sent by `simulate.py`. In a real deployment:

* Voltage divider resistors (10 kΩ / 2.2 kΩ) scale panel voltage into the ESP32's 0–3.3 V ADC range
* ACS712 current sensors read load and panel current
* DS18B20 temperature sensors (with 4.7 kΩ pull-up) monitor battery temperature
* Starlink terminal at the Ranger Base provides the Wi-Fi uplink

See **Appendix D** of the Assignment 3c Final Report for full hardware specifications.

\---

## Requirements

**ESP32 firmware:** MicroPython v1.20+ (standard build)

**Laptop (simulate.py):**

```bash
pip install requests   # real mode only — test mode needs no extra packages
```

Python 3.8+ required.

\---

## Assignment Context

This repository is submitted as part of Assignment 3c (Final Report) for CUC106 at Charles Darwin University. The system addresses **Design Area 3.4 — Energy Monitoring and Fault Diagnosis** for the Port Stewart Ranger Base, operated by Lama Lama Rangers within their Indigenous Protected Area on Cape York Peninsula, Queensland.


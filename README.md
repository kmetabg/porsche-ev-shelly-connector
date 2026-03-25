# Porsche EV — Shelly Connector

Connect your **Porsche Taycan, Macan EV, Panamera PHEV** (any Porsche Connect vehicle) to **Shelly virtual components** via the unofficial Porsche Connect API.

> ⚠️ This uses an unofficial API. Porsche may change it at any time. An active **Porsche Connect subscription** is required.

---

## What it does

A lightweight FastAPI server acts as a bridge between Porsche Connect and your Shelly device:

- **Battery level** → `number:200`
- **Climate control** (start/stop with temperature) → `boolean:200` toggle
- **Lock status** → `boolean:201`
- **Doors/lids** → `boolean:202`
- **Charging power (kW)** → `number:202`
- **Dashboard** with battery gauge, live status, climate control buttons

![Shelly components showing battery 86%, climate toggle, locked status, doors closed, charging kW](.github/preview.png)

---

## Quick Start — Render.com (Free, no server needed)

> Best option for non-technical users. **Free tier** — kept alive by the Shelly 10-min poll.

### Step 1 — Fork this repo

Click **Fork** (top right) → Fork to your GitHub account.

### Step 2 — Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

Or manually:
1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub account → select your fork
3. Render auto-detects the `Dockerfile` ✓
4. Set these **Environment Variables**:

| Variable | Value |
|---|---|
| `PORSCHE_EMAIL` | your-myporsche@email.com |
| `PORSCHE_PASSWORD` | your-password |
| `PORSCHE_OVERVIEW_MODE` | `stored` |
| `REFRESH_INTERVAL_MINUTES` | `10` |
| `PORSCHE_TOKEN_FILE` | `/tmp/porsche_token.json` |

5. Click **Deploy** → wait ~2 min
6. Copy your URL: `https://porsche-connect-xxxx.onrender.com`

### Step 3 — First login

Open your Render URL in browser → you'll see the login page.

**Default password: `porsche`** → change it immediately in Settings!

### Step 4 — Add your Porsche credentials

Settings → **My Porsche Account** → enter email + password → Save.

### Step 5 — Copy your API key

Settings → **API Key** → copy it (you'll need it for the Shelly script).

---

## Shelly Setup

### Required: Shelly Pro or Gen2+ device with firmware ≥ 1.1

### Step 1 — Create virtual components

Go to your Shelly web UI → **Components** → **Add virtual component**:

| Type | ID | Label | Notes |
|---|---|---|---|
| Number | 200 | Battery % | view: label |
| Number | 201 | Climate temp °C | view: slider, min: 10, max: 30 |
| Number | 202 | Charging kW | view: label |
| Boolean | 200 | Climate | view: toggle |
| Boolean | 201 | Locked | view: label |
| Boolean | 202 | Doors closed | view: label |

### Step 2 — Install the script

1. Shelly web UI → **Scripts** → **Create script**
2. Name: `Porsche Connect`
3. Paste the contents of [`shelly_porsche.js`](./shelly_porsche.js)
4. Edit the top 3 variables:

```javascript
var API_BASE = "https://YOUR-RENDER-URL.onrender.com";
var API_KEY  = "your-api-key-from-settings";
var VIN      = "WP0ZZZY1XXXXXXXX";   // Your VIN (found in My Porsche app)
```

5. **Save** → **Start**

### Step 3 — Verify

In the script console you should see:
```
[Porsche] Script started. VIN=WP0ZZZY... poll=600s
[Porsche] Polling...
[Porsche] Battery: 86%
[Porsche] Climate ON: false
[Porsche] Locked: true
[Porsche] Doors closed: true
[Porsche] Charging kW: 0
```

---

## Self-hosting with Docker

```bash
git clone https://github.com/kmetabg/porsche-ev-shelly-connector.git
cd porsche-ev-shelly-connector
cp .env.example .env
# Edit .env with your credentials
docker compose up -d
```

Open `http://localhost:8000` → login with password `porsche`.

---

## Supported vehicles

Any vehicle with **Porsche Connect** subscription:
- Taycan (all variants)
- Macan EV (2024+)
- Panamera (2021+, PHEV)
- Cayenne (2017+, E3)
- 911 (992+)
- Boxster / Cayman 718

---

## API Endpoints

All endpoints require `?api_key=YOUR_KEY` or header `X-API-Key: YOUR_KEY`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Server status |
| GET | `/vehicles` | List vehicles |
| GET | `/vehicles/{vin}/battery` | Battery, charging, doors, lock status |
| GET | `/vehicles/{vin}/climate/start?temperature=21` | Start climate (async) |
| GET | `/vehicles/{vin}/climate/stop` | Stop climate (async) |
| POST | `/refresh` | Force cache refresh |

---

## Captcha handling

If Porsche requires captcha during login, the server returns HTTP `428` with a base64 captcha image. Solve it in the dashboard Settings page.

---

## Credits

- [pyporscheconnectapi](https://github.com/CJNE/pyporscheconnectapi) by Johan Isaksson — the Python library making this possible
- Porsche Connect API reverse-engineered by the community

---

## Disclaimer

This project is not affiliated with or endorsed by Porsche AG. Use at your own risk.

# Porsche Taycan Docker Starter

Docker-first starter project for reading Porsche Connect data from a Taycan through the same account flow used by My Porsche.

Important notes:

- This relies on an unofficial client library: `pyporscheconnectapi`
- Porsche can change endpoints, OAuth2 details, or captcha flow at any time
- An active Porsche Connect subscription is required
- `stored` overview mode is usually better for normal polling because it avoids waking the car

## What This Project Does

This starter runs a small FastAPI service in Docker and exposes a few endpoints:

- `GET /health`
- `GET /vehicles`
- `GET /vehicles?include_overview=true`
- `GET /vehicles/{vin}/overview`
- `GET /vehicles/{vin}/trip-statistics`
- `GET /vehicles/{vin}/capabilities`

The service keeps the OAuth token in `./data/token.json` so you do not need a full login on every request.

## Quick Start

1. Create your local env file:

```bash
cp .env.example .env
```

2. Fill in your My Porsche email and password in `.env`

3. Start the project:

```bash
docker compose up --build
```

4. Open the API docs:

[http://localhost:8000/docs](http://localhost:8000/docs)

## Example Requests

List vehicles:

```bash
curl http://localhost:8000/vehicles
```

Read cached overview:

```bash
curl http://localhost:8000/vehicles/<VIN>/overview
```

Force fresher data by waking the car:

```bash
curl "http://localhost:8000/vehicles/<VIN>/overview?mode=current"
```

Get trip statistics:

```bash
curl http://localhost:8000/vehicles/<VIN>/trip-statistics
```

## Captcha Handling

Porsche auth may sometimes require a captcha.

If that happens, the API returns HTTP `428` with a response like:

```json
{
  "detail": {
    "error": "captcha_required",
    "message": "Porsche auth requested a captcha challenge.",
    "captcha": "data:image/svg+xml;base64,...",
    "state": "..."
  }
}
```

You can then retry the same request with the captcha solution:

```bash
curl "http://localhost:8000/vehicles?captcha_code=<CODE>&state=<STATE>"
```

## Environment Variables

Defined in `.env.example`:

- `PORSCHE_EMAIL`
- `PORSCHE_PASSWORD`
- `PORSCHE_OVERVIEW_MODE=stored|current`
- `PORSCHE_TOKEN_FILE=/app/data/token.json`
- `PORT=8000`

## Suggested Next Steps

- Add a scheduled poller that stores snapshots in SQLite or Postgres
- Add Prometheus metrics for battery, range, lock status, and charging state
- Add a simple dashboard or Home Assistant bridge on top of this API

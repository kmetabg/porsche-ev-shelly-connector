from __future__ import annotations

import asyncio
import httpx
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.exceptions import (
    PorscheCaptchaRequiredError,
    PorscheExceptionError,
    PorscheWrongCredentialsError,
)
from pyporscheconnectapi.vehicle import PorscheVehicle

from .config import (
    check_dashboard_password,
    credentials_configured,
    get_api_key,
    get_porsche_credentials,
    get_session_secret,
    is_default_password,
    rotate_api_key,
    save_dashboard_password,
    save_porsche_credentials,
)

_log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

TOKEN_FILE = Path(os.getenv("PORSCHE_TOKEN_FILE", "/app/data/token.json"))
TOKEN_LOCK = asyncio.Lock()

REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL_MINUTES", "15")) * 60

# ── In-memory cache ───────────────────────────────────────────────────────────

_cache: dict[str, Any] = {
    "vehicles": [],        # list[dict] — last known vehicle summaries
    "last_updated": None,  # datetime | None
    "refreshing": False,
}
_refresh_lock = asyncio.Lock()

# ── Lifespan: background poller ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    task = asyncio.create_task(_background_poller())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _background_poller():
    """Refresh vehicle data every REFRESH_INTERVAL seconds."""
    await asyncio.sleep(5)   # short startup delay
    while True:
        if credentials_configured():
            try:
                await _do_refresh()
            except Exception as exc:
                _log.warning("Background refresh failed: %s", exc)
        await asyncio.sleep(REFRESH_INTERVAL)


async def _do_refresh() -> list[dict]:
    """Fetch all vehicles + stored overview and update the cache."""
    async with _refresh_lock:
        if _cache["refreshing"]:
            # Another coroutine already refreshing — wait for it
            while _cache["refreshing"]:
                await asyncio.sleep(0.5)
            return _cache["vehicles"]

        _cache["refreshing"] = True

    try:
        account = await _build_account()
        vehicles = await account.get_vehicles()
        summaries = []
        for v in vehicles:
            await v.get_stored_overview()
            summaries.append(_vehicle_summary(v, include_raw=False))
        _cache["vehicles"] = summaries
        _cache["last_updated"] = datetime.now(timezone.utc)
        return summaries
    finally:
        _cache["refreshing"] = False
        await _close_account(account if "account" in dir() else None)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Porsche Connect API",
    version="0.2.0",
    description="Docker-first Porsche Connect integration.",
    lifespan=lifespan,
)

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Auth middleware registered first → becomes INNERMOST → runs after session MW
_PUBLIC = {"/login", "/simple"}
_PUBLIC_PREFIXES = ("/static",)
_API_PREFIXES = ("/vehicles", "/health", "/refresh", "/openapi.json")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # API key auth (for Shelly / programmatic callers)
    api_key_header = request.headers.get("X-API-Key", "")
    api_key_query  = request.query_params.get("api_key", "")
    if api_key_header or api_key_query:
        if (api_key_header or api_key_query) == get_api_key():
            return await call_next(request)
        return JSONResponse({"error": "invalid_api_key"}, status_code=403)

    # Session auth
    if not request.session.get("authenticated"):
        if any(path.startswith(p) for p in _API_PREFIXES):
            return JSONResponse({"error": "unauthorized", "message": "Login required."}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    return await call_next(request)


# Session MW is OUTERMOST → runs first → populates request.session
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    session_cookie="porsche_session",
    max_age=86_400 * 7,
    https_only=False,
)

# ── Token helpers ─────────────────────────────────────────────────────────────

async def _load_token() -> dict[str, Any]:
    async with TOKEN_LOCK:
        try:
            if TOKEN_FILE.exists():
                return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, PermissionError):
            pass
    return {}


async def _save_token(token: dict[str, Any]) -> None:
    if not token or not token.get("access_token"):
        return
    async with TOKEN_LOCK:
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(
                json.dumps(token, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except (OSError, PermissionError):
            return

# ── Porsche account helpers ───────────────────────────────────────────────────

class ConfigError(RuntimeError):
    pass


def _get_credentials() -> tuple[str, str]:
    email, password = get_porsche_credentials()
    if not email or not password:
        raise ConfigError("My Porsche credentials are not configured. Go to Settings.")
    return email, password


def _get_default_overview_mode() -> Literal["stored", "current"]:
    configured = os.getenv("PORSCHE_OVERVIEW_MODE", "stored").strip().lower()
    return configured if configured in {"stored", "current"} else "stored"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


async def _build_account(captcha_code: str | None = None, state: str | None = None) -> PorscheConnectAccount:
    email, password = _get_credentials()
    token = await _load_token()
    connection = Connection(
        email=email,
        password=password,
        captcha_code=captcha_code,
        state=state,
        async_client=httpx.AsyncClient(),
        token=token,
    )
    return PorscheConnectAccount(connection=connection)


async def _close_account(account: PorscheConnectAccount | None) -> None:
    if account is None:
        return
    try:
        await _save_token(dict(account.connection.token))
    finally:
        await account.connection.close()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConfigError):
        return HTTPException(status_code=500, detail={"error": "missing_configuration", "message": str(exc)})
    if isinstance(exc, PorscheWrongCredentialsError):
        return HTTPException(status_code=401, detail={"error": "wrong_credentials"})
    if isinstance(exc, PorscheCaptchaRequiredError):
        return HTTPException(status_code=428, detail={"error": "captcha_required", "captcha": exc.captcha, "state": exc.state})
    if isinstance(exc, PorscheExceptionError):
        return HTTPException(status_code=502, detail={"error": "porsche_api_error", "message": exc.message or str(exc)})
    return HTTPException(status_code=500, detail={"error": "unexpected_error", "message": str(exc)})


async def _load_overview(vehicle: PorscheVehicle, mode: Literal["stored", "current"]) -> None:
    if mode == "current":
        await vehicle.get_current_overview()
    else:
        await vehicle.get_stored_overview()


def _vehicle_summary(vehicle: PorscheVehicle, *, include_raw: bool = False) -> dict[str, Any]:
    data = vehicle.get_data()
    engine = data.get("modelType", {}).get("engine")
    is_ev = engine in {"BEV", "PHEV"}
    has_open = any(k.startswith("OPEN_STATE_") for k in data)
    has_loc = "GPS_LOCATION" in data
    lat, lon, heading = vehicle.location

    charging = data.get("CHARGING_SUMMARY", {})
    charging_rate = data.get("CHARGING_RATE", {})
    e_range = data.get("E_RANGE", {})
    mileage = data.get("MILEAGE", {})
    climate = data.get("CLIMATIZER_STATE", {})

    payload: dict[str, Any] = {
        "vin": vehicle.vin,
        "name": data.get("name") or data.get("customName") or vehicle.model_name,
        "model_name": vehicle.model_name,
        "model_year": vehicle.model_year,
        "engine": engine,
        "connected": data.get("connect"),
        # Battery & charging
        "battery_level": vehicle.main_battery_level if is_ev else None,
        "charging_target": charging.get("minSoC"),
        "charging_mode": charging.get("mode"),
        "charging_power_kw": charging_rate.get("chargingPower"),
        "charging_rate_kph": charging_rate.get("chargingRate-kph"),
        "charge_target_time": _json_safe(charging.get("targetDateTimeWithOffset")),
        # Range & mileage
        "electric_range_km": e_range.get("distance"),
        "mileage_km": mileage.get("mileage"),
        # Status
        "vehicle_locked": vehicle.vehicle_locked if "LOCK_STATE_VEHICLE" in data else None,
        "vehicle_closed": vehicle.vehicle_closed if has_open else None,
        "doors_and_lids": vehicle.doors_and_lids if has_open else {},
        "direct_charge_on": vehicle.direct_charge_on if "CHARGING_SUMMARY" in data else None,
        "climate_on": climate.get("isOn"),
        "climate_target_temp_c": _kelvin_to_celsius(climate.get("targetTemperature")),
        # Location
        "location": (
            {"latitude": lat, "longitude": lon, "heading": heading,
             "updated_at": _json_safe(vehicle.location_updated_at)}
            if has_loc else None
        ),
    }
    if include_raw:
        payload["raw"] = _json_safe(data)
    return payload


def _kelvin_to_celsius(k: float | None) -> float | None:
    if k is None:
        return None
    return round(k - 273.15, 1)


def _celsius_to_kelvin(c: float) -> float:
    return c + 273.15

# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/login")
async def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "error": None,
        "default_password": is_default_password(),
    })


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if check_dashboard_password(password):
        request.session["authenticated"] = True
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "error": "Wrong password. Please try again.",
        "default_password": is_default_password(),
    }, status_code=401)


@app.get("/simple")
async def simple_get(request: Request):
    """Token helper page — enter Porsche credentials, get refresh token for Shelly direct script."""
    return templates.TemplateResponse(request, "simple.html", {
        "token": None, "vehicles": [], "error": None,
        "captcha": None, "captcha_state": None,
        "prefill_email": None,
    })


@app.post("/simple")
async def simple_post(
    request: Request,
    email:        str = Form(...),
    password:     str = Form(...),
    captcha_code: str = Form(""),
    captcha_state:str = Form(""),
):
    """Authenticate with Porsche, return refresh token — nothing is stored."""
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient() as client:
            conn = Connection(
                email=email.strip(),
                password=password,
                captcha_code=captcha_code.strip() or None,
                state=captcha_state.strip() or None,
                async_client=client,
            )
            acc = PorscheConnectAccount(connection=conn)
            vehicles = await acc.get_vehicles()
            token_dict = dict(conn.token)
            refresh_token = token_dict.get("refresh_token", "")
            if not refresh_token:
                raise ValueError("No refresh token received.")
        return templates.TemplateResponse(request, "simple.html", {
            "token": refresh_token,
            "vehicles": vehicles,
            "error": None,
            "captcha": None, "captcha_state": None,
            "prefill_email": None,
        })
    except PorscheWrongCredentialsError:
        return templates.TemplateResponse(request, "simple.html", {
            "token": None, "vehicles": [],
            "error": "Wrong email or password.",
            "captcha": None, "captcha_state": None,
            "prefill_email": email,
        }, status_code=401)
    except PorscheCaptchaRequiredError as exc:
        return templates.TemplateResponse(request, "simple.html", {
            "token": None, "vehicles": [],
            "error": None,
            "captcha": exc.captcha,      # base64 image (data:image/svg+xml;base64,...)
            "captcha_state": exc.state,
            "prefill_email": email,
        }, status_code=200)
    except Exception as exc:
        return templates.TemplateResponse(request, "simple.html", {
            "token": None, "vehicles": [],
            "error": f"Error: {exc}",
            "captcha": None, "captcha_state": None,
            "prefill_email": email,
        }, status_code=500)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD & SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/dashboard")
async def dashboard(request: Request):
    email, _ = get_porsche_credentials()
    return templates.TemplateResponse(request, "dashboard.html", {
        "creds_ok": credentials_configured(),
        "last_updated": _cache["last_updated"].isoformat() if _cache["last_updated"] else None,
        "vehicles": _cache["vehicles"],
        "default_password": is_default_password(),
        "api_key": get_api_key(),
    })


@app.get("/settings")
async def settings_page(request: Request):
    email, _ = get_porsche_credentials()
    return templates.TemplateResponse(request, "settings.html", {
        "creds_ok": credentials_configured(),
        "porsche_email": email or "",
        "default_password": is_default_password(),
        "api_key": get_api_key(),
        "success": request.session.pop("flash_ok", None),
        "error": request.session.pop("flash_err", None),
    })


@app.post("/settings/porsche")
async def settings_porsche(request: Request, email: str = Form(...), password: str = Form("")):
    if not email.strip():
        request.session["flash_err"] = "Email cannot be empty."
        return RedirectResponse("/settings", status_code=302)
    existing_email, existing_password = get_porsche_credentials()
    final_password = password.strip() or (existing_password or "")
    if not final_password:
        request.session["flash_err"] = "Enter your My Porsche password."
        return RedirectResponse("/settings", status_code=302)
    save_porsche_credentials(email.strip(), final_password)
    request.session["flash_ok"] = "My Porsche credentials saved successfully."
    return RedirectResponse("/settings", status_code=302)


@app.post("/settings/password")
async def settings_password(request: Request, new_password: str = Form(...), confirm_password: str = Form(...)):
    if len(new_password) < 6:
        request.session["flash_err"] = "Password must be at least 6 characters."
        return RedirectResponse("/settings", status_code=302)
    if new_password != confirm_password:
        request.session["flash_err"] = "Passwords do not match."
        return RedirectResponse("/settings", status_code=302)
    save_dashboard_password(new_password)
    request.session["flash_ok"] = "Password changed successfully."
    return RedirectResponse("/settings", status_code=302)


@app.post("/settings/rotate-api-key")
async def settings_rotate_api_key(request: Request):
    rotate_api_key()
    request.session["flash_ok"] = "API key rotated successfully."
    return RedirectResponse("/settings", status_code=302)

# ══════════════════════════════════════════════════════════════════════════════
# CORE API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "credentials_configured": credentials_configured(),
        "cache_last_updated": _cache["last_updated"].isoformat() if _cache["last_updated"] else None,
        "vehicles_cached": len(_cache["vehicles"]),
    }


@app.post("/refresh")
async def manual_refresh() -> dict[str, Any]:
    """Trigger an immediate refresh of cached vehicle data."""
    try:
        summaries = await _do_refresh()
        return {
            "status": "ok",
            "last_updated": _cache["last_updated"].isoformat() if _cache["last_updated"] else None,
            "vehicles": summaries,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


# ── Vehicle list ──────────────────────────────────────────────────────────────

@app.get("/vehicles")
async def list_vehicles(
    include_overview: bool = Query(default=False),
    mode: Literal["stored", "current"] | None = Query(default=None),
    captcha_code: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> dict[str, Any]:
    if not include_overview and _cache["vehicles"]:
        return {
            "count": len(_cache["vehicles"]),
            "from_cache": True,
            "last_updated": _cache["last_updated"].isoformat() if _cache["last_updated"] else None,
            "vehicles": _cache["vehicles"],
        }
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account(captcha_code=captcha_code, state=state)
        vehicles = await account.get_vehicles()
        selected_mode = mode or _get_default_overview_mode()
        if include_overview:
            for vehicle in vehicles:
                await _load_overview(vehicle, selected_mode)
        summaries = [_vehicle_summary(v) for v in vehicles]
        return {"count": len(summaries), "from_cache": False, "vehicles": summaries}
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await _close_account(account)


@app.get("/vehicles/{vin}/overview")
async def vehicle_overview(
    vin: str,
    mode: Literal["stored", "current"] | None = Query(default=None),
    captcha_code: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> dict[str, Any]:
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account(captcha_code=captcha_code, state=state)
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await _load_overview(vehicle, mode or _get_default_overview_mode())
        return {"vehicle": _vehicle_summary(vehicle, include_raw=True)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await _close_account(account)


# ── Battery ───────────────────────────────────────────────────────────────────

def _battery_payload(source: dict, from_cache: bool, vin: str) -> dict[str, Any]:
    """Build the unified battery/status response dict from a vehicle summary dict."""
    charging_kw  = source.get("charging_power_kw") or 0
    charging_kph = source.get("charging_rate_kph") or 0
    mode         = source.get("charging_mode")

    # Plugged in = port open / charging mode present (doesn't mean current is flowing)
    plugged_in  = mode in {"DIRECT", "PROFILE", "DELAYED", "TIMER"}
    # Actively charging = actual power flowing
    is_charging = charging_kw > 0 or charging_kph > 0

    return {
        "vin": vin,
        "from_cache": from_cache,
        "last_updated": _cache["last_updated"].isoformat() if _cache["last_updated"] else None,
        # Battery
        "battery_level": source.get("battery_level"),
        "electric_range_km": source.get("electric_range_km"),
        "mileage_km": source.get("mileage_km"),
        # Charging
        "plugged_in":       plugged_in,           # кабелът е включен
        "is_charging":      is_charging,           # тече реален ток
        "charging_power_kw": charging_kw,
        "charging_rate_kph": charging_kph,
        "charging_mode":    mode,
        "charging_target":  source.get("charging_target"),
        "charge_target_time": source.get("charge_target_time"),
        # Climate
        "climate_on":            source.get("climate_on"),
        "climate_target_temp_c": source.get("climate_target_temp_c"),
        # Lock & doors
        "vehicle_locked": source.get("vehicle_locked"),
        "vehicle_closed": source.get("vehicle_closed"),
        "doors_and_lids": source.get("doors_and_lids", {}),
    }


@app.get("/vehicles/{vin}/battery")
async def vehicle_battery(vin: str) -> dict[str, Any]:
    """Return battery, charging, lock and doors status (from cache when available)."""
    cached = next((v for v in _cache["vehicles"] if v["vin"] == vin), None)
    if cached:
        return _battery_payload(cached, from_cache=True, vin=vin)

    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await vehicle.get_stored_overview()
        summary = _vehicle_summary(vehicle)
        return _battery_payload(summary, from_cache=False, vin=vin)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await _close_account(account)


# ── Climate ───────────────────────────────────────────────────────────────────

def _update_vehicle_in_cache(vehicle: PorscheVehicle) -> None:
    """Replace the cached entry for this vehicle with fresh data from the vehicle object."""
    summary = _vehicle_summary(vehicle)
    for i, v in enumerate(_cache["vehicles"]):
        if v["vin"] == vehicle.vin:
            _cache["vehicles"][i] = summary
            _cache["last_updated"] = datetime.now(timezone.utc)
            return
    # Not in cache yet — append it
    _cache["vehicles"].append(summary)
    _cache["last_updated"] = datetime.now(timezone.utc)


async def _run_climate_command(vin: str, action: str, temperature: float | None = None) -> None:
    """Background task: send climate command to Porsche, update cache when done."""
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            _log.warning("Climate background task: VIN %s not found", vin)
            return
        await vehicle.get_stored_overview()

        try:
            if action == "start" and temperature is not None:
                await asyncio.wait_for(
                    vehicle.remote_services.climatise_on(
                        target_temperature=_celsius_to_kelvin(temperature)
                    ),
                    timeout=60.0,
                )
            else:
                await asyncio.wait_for(
                    vehicle.remote_services.climatise_off(),
                    timeout=60.0,
                )
        except asyncio.TimeoutError:
            _log.warning("Climate command timed out after 60s — fetching current status")
            await vehicle.get_stored_overview()

        _update_vehicle_in_cache(vehicle)
        _log.info("Climate %s done. climate_on=%s", action, vehicle.remote_climatise_on)

    except Exception as exc:
        _log.warning("Climate background task error: %s", exc)
    finally:
        await _close_account(account)


@app.get("/vehicles/{vin}/climate/start")
@app.post("/vehicles/{vin}/climate/start")
async def climate_start(
    vin: str,
    temperature: float = Query(default=20.0, ge=10.0, le=30.0, description="Target temperature °C"),
) -> dict[str, Any]:
    """Start climatisation — returns immediately, command runs in background (≤60s)."""
    # Verify VIN exists in cache or raise early
    if not credentials_configured():
        raise HTTPException(status_code=500, detail={"error": "missing_configuration"})

    asyncio.create_task(_run_climate_command(vin, "start", temperature))
    return {
        "vin": vin,
        "action": "climate_start",
        "target_temperature_c": temperature,
        "status": "PENDING",
        "message": "Command accepted. Result will be available in ~30-60s on next poll.",
    }


@app.get("/vehicles/{vin}/climate/stop")
@app.post("/vehicles/{vin}/climate/stop")
async def climate_stop(vin: str) -> dict[str, Any]:
    """Stop climatisation — returns immediately, command runs in background (≤60s)."""
    if not credentials_configured():
        raise HTTPException(status_code=500, detail={"error": "missing_configuration"})

    asyncio.create_task(_run_climate_command(vin, "stop"))
    return {
        "vin": vin,
        "action": "climate_stop",
        "status": "PENDING",
        "message": "Command accepted. Result will be available in ~30-60s on next poll.",
    }


# kept for internal use (dashboard buttons still work synchronously)
async def _climate_start_sync(vin: str, temperature: float) -> dict[str, Any]:
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await vehicle.get_stored_overview()
        status = await vehicle.remote_services.climatise_on(
            target_temperature=_celsius_to_kelvin(temperature)
        )
        _update_vehicle_in_cache(vehicle)
        return {"vin": vin, "action": "climate_start", "status": status.state.value if status else "SENT", "climate_on": vehicle.remote_climatise_on}
    finally:
        await _close_account(account)


async def _climate_stop_sync(vin: str) -> dict[str, Any]:
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await vehicle.get_stored_overview()
        status = await vehicle.remote_services.climatise_off()
        _update_vehicle_in_cache(vehicle)
        return {"vin": vin, "action": "climate_stop", "status": status.state.value if status else "SENT", "climate_on": vehicle.remote_climatise_on}
    finally:
        await _close_account(account)


# Dashboard sync endpoints (used by browser buttons)
@app.post("/vehicles/{vin}/climate/start-sync")
async def climate_start_sync(
    vin: str,
    temperature: float = Query(default=20.0, ge=10.0, le=30.0),
) -> dict[str, Any]:
    try:
        return await _climate_start_sync(vin, temperature)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/vehicles/{vin}/climate/stop-sync")
async def climate_stop_sync(vin: str) -> dict[str, Any]:
    try:
        return await _climate_stop_sync(vin)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/vehicles/{vin}/trip-statistics")
async def vehicle_trip_statistics(vin: str) -> dict[str, Any]:
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await vehicle.get_trip_statistics()
        return {"vin": vin, "trip_statistics": _json_safe(vehicle.trip_statistics)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await _close_account(account)


@app.get("/vehicles/{vin}/capabilities")
async def vehicle_capabilities(vin: str) -> dict[str, Any]:
    account: PorscheConnectAccount | None = None
    try:
        account = await _build_account()
        vehicle = await account.get_vehicle(vin)
        if vehicle is None:
            raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
        await vehicle.get_capabilities()
        return {"vin": vin, "capabilities": _json_safe(vehicle.capabilities)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc
    finally:
        await _close_account(account)

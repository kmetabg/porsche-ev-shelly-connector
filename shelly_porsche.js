/**
 * Porsche Connect — Shelly Script
 * ─────────────────────────────────────────────────────────────────────────────
 * Виртуални компоненти (Components → Add virtual component):
 *
 *   Number  id=200  label="Battery %"        read-only
 *   Number  id=201  label="Climate temp °C"  min=10  max=30  default=20
 *   Boolean id=200  label="Climate"          TOGGLE
 *   Boolean id=201  label="Locked"           read-only
 *   Boolean id=202  label="Doors closed"     read-only
 *   Boolean id=203  label="Charging"         read-only
 * ─────────────────────────────────────────────────────────────────────────────
 */

var API_BASE = "https://YOUR-RENDER-URL.onrender.com";  // ← замени с твоя URL
var API_KEY  = "YOUR-API-KEY";                          // ← от Settings → API Key
var VIN      = "YOUR-VIN";                              // ← VIN от My Porsche app
var POLL_SEC = 600;    // 10 мин — Render free не заспива (sleep след 15 мин без заявки)

// Виртуални компоненти — получаваме handles веднъж при старт
var vBattery    = Virtual.getHandle("number:200");   // Battery %
var vTempSet    = Virtual.getHandle("number:201");   // Climate temp °C
var vChargingKw = Virtual.getHandle("number:202");   // Charging kW
var vClimate    = Virtual.getHandle("boolean:200");  // Climate toggle
var vLocked     = Virtual.getHandle("boolean:201");  // Locked
var vDoors      = Virtual.getHandle("boolean:202");  // Doors closed
var vCharging   = Virtual.getHandle("boolean:203");  // Actively charging (power > 0)

// Флаг: скриптът задава vClimate → игнорираме "change" event
var _climateUpdating = false;

// ── HTTP helpers ──────────────────────────────────────────────────────────────

function apiGet(path, cb) {
  var sep = (path.indexOf("?") >= 0) ? "&" : "?";
  Shelly.call("HTTP.GET", {
    url: API_BASE + path + sep + "api_key=" + API_KEY,
    timeout: 30,
    ssl_ca: "*"
  }, function(res, err) {
    if (err || !res || res.code !== 200) {
      print("[Porsche] GET failed (" + (err || res.code) + "): " + path);
      cb(null);
      return;
    }
    try { cb(JSON.parse(res.body)); }
    catch(e) { print("[Porsche] JSON err: " + e); cb(null); }
  });
}

function apiPost(path, cb, _retry) {
  if (!_retry) { _retry = 0; }
  var sep = (path.indexOf("?") >= 0) ? "&" : "?";
  Shelly.call("HTTP.POST", {
    url: API_BASE + path + sep + "api_key=" + API_KEY,
    timeout: 90,
    ssl_ca: "*",
    body: ""
  }, function(res, err) {
    if (err || !res || res.code !== 200) {
      if (_retry < 2 && (err === -103 || err === -101)) {
        print("[Porsche] POST retry " + (_retry + 1) + ": " + path);
        Timer.set(3000, false, function() { apiPost(path, cb, _retry + 1); });
        return;
      }
      print("[Porsche] POST failed (" + (err || res.code) + "): " + path);
      cb(null);
      return;
    }
    try { cb(JSON.parse(res.body)); }
    catch(e) { print("[Porsche] JSON err: " + e); cb(null); }
  });
}

// ── Poll — верижни callbacks, max 2 таймера едновременно ─────────────────────

function pollVehicle() {
  print("[Porsche] Polling...");
  apiGet("/vehicles/" + VIN + "/battery", function(data) {
    if (!data) return;

    // Стъпка 1: Battery (setValue е асинхронен — без callback проблеми)
    var batt = data.battery_level;
    if (typeof batt === "number" && vBattery) {
      vBattery.setValue(batt);
      print("[Porsche] Battery: " + batt + "%");
    }

    // Стъпка 2: Climate — задаваме флаг преди setValue
    var climOn = (data.climate_on === true);
    _climateUpdating = true;
    if (vClimate) {
      vClimate.setValue(climOn);
      // Нормален poll: показваме реалния статус с подходящи label-и
      if (climOn) { setClimateLabels("OFF", "Started ✓"); }
      else        { setClimateLabels("OFF", "ON");        }
    }
    print("[Porsche] Climate ON: " + climOn);

    // Стъпка 3-7: верижно с таймери (само 1 таймер в даден момент)
    Timer.set(300, false, function() {
      if (vLocked) { vLocked.setValue(data.vehicle_locked === true); }
      print("[Porsche] Locked: " + data.vehicle_locked);

      Timer.set(300, false, function() {
        if (vDoors) { vDoors.setValue(data.vehicle_closed === true); }
        print("[Porsche] Doors closed: " + data.vehicle_closed);

        Timer.set(300, false, function() {
          // boolean:203 — един компонент, динамичен label за всички charging статуси
          var plugged = (data.plugged_in === true);
          var chg     = (data.is_charging === true);
          var kw      = data.charging_power_kw || 0;
          var kwStr   = kw > 0 ? (Math.round(kw * 10) / 10) + " kW" : "";

          var trueLabel;
          if (chg && kw > 0) {
            trueLabel = "Charging " + kwStr;  // "Charging 7.4 kW"
          } else if (plugged) {
            trueLabel = "Plugged in";          // включен, но не зарежда
          } else {
            trueLabel = "Connected";
          }

          // number:202 — Charging kW
          if (vChargingKw) { vChargingKw.setValue(kw); }

          // boolean:203 — Actively charging (true = power flowing)
          if (vCharging) { vCharging.setValue(chg); }

          print("[Porsche] Charger: plugged=" + plugged + " charging=" + chg + " " + kwStr);
          _climateUpdating = false;  // флагът се нулира накрая
        });
      });
    });
  });
}

// ── Start / Stop климатизация ─────────────────────────────────────────────────

// ── Динамична промяна на label-ите на boolean:200 ─────────────────────────────

function setClimateLabels(falseLabel, trueLabel) {
  if (!vClimate) { return; }
  vClimate.setConfig({ meta: { ui: { view: "toggle", titles: [falseLabel, trueLabel] } } });
}

// ── Polling след команда: проверява на 5 сек, спира при промяна ──────────────

function waitForClimateChange(expectedOn, attemptsLeft) {
  if (attemptsLeft <= 0) {
    print("[Porsche] Climate poll timeout — final refresh");
    // Върни нормалните label-и
    if (expectedOn) { setClimateLabels("OFF", "Started"); }
    else             { setClimateLabels("OFF", "Started"); }
    refreshAndPoll();
    return;
  }
  apiGet("/vehicles/" + VIN + "/battery", function(data) {
    if (!data) {
      Timer.set(5000, false, function() { waitForClimateChange(expectedOn, attemptsLeft - 1); });
      return;
    }
    var current = (data.climate_on === true);
    print("[Porsche] Waiting... climate_on=" + current + " (want " + expectedOn + ", " + attemptsLeft + " left)");

    if (current === expectedOn) {
      // Потвърдено — обнови label и стойност
      print("[Porsche] Climate confirmed: " + current);
      if (current) {
        setClimateLabels("OFF", "Started ✓");
      } else {
        setClimateLabels("OFF", "Started");
      }
      _climateUpdating = true;
      if (vClimate) { vClimate.setValue(current); }
      Timer.set(500, false, function() { _climateUpdating = false; });
    } else {
      // Още не е готово — провери пак след 5 сек
      Timer.set(5000, false, function() { waitForClimateChange(expectedOn, attemptsLeft - 1); });
    }
  });
}

function afterCommand(label, data, expectedOn) {
  if (!data) return;
  print("[Porsche] " + label + ": " + data.status);

  if (data.status === "PENDING") {
    // Покажи "Pending..." докато чакаме потвърждение от колата
    if (expectedOn) {
      setClimateLabels("OFF", "Pending...");
    } else {
      setClimateLabels("Stopping...", "Started");
    }
    print("[Porsche] Polling for result every 5s (max 70s)...");
    Timer.set(5000, false, function() { waitForClimateChange(expectedOn, 14); });
    return;
  }

  // Синхронен отговор (рядко)
  var climOn = (data.climate_on === true);
  if (climOn) {
    setClimateLabels("OFF", "Started ✓");
  } else {
    setClimateLabels("OFF", "Started");
  }
  _climateUpdating = true;
  if (vClimate) { vClimate.setValue(climOn); }
  Timer.set(500, false, function() { _climateUpdating = false; });
}

function startClimate() {
  var temp = 20;
  if (vTempSet) {
    var v = vTempSet.getValue();
    if (typeof v === "number" && v >= 10 && v <= 30) { temp = v; }
  }
  print("[Porsche] → START climate at " + temp + "°C...");
  apiGet("/vehicles/" + VIN + "/climate/start?temperature=" + temp, function(data) {
    afterCommand("START @ " + temp + "°C", data, true);   // очакваме climate_on=true
  });
}

function stopClimate() {
  print("[Porsche] → STOP climate...");
  apiGet("/vehicles/" + VIN + "/climate/stop", function(data) {
    afterCommand("STOP", data, false);   // очакваме climate_on=false
  });
}

// ── Reset конфиги при старт ───────────────────────────────────────────────────

if (vClimate) {
  vClimate.setConfig({
    name: "Climate",
    persisted: false,
    default_value: false,
    meta: { ui: { view: "toggle", titles: ["OFF", "ON"] } }
  });
  print("[Porsche] boolean:200 config reset OK");
}

if (vChargingKw) {
  vChargingKw.setConfig({
    name: "Charging kW",
    meta: { ui: { view: "label" } }
  });
  print("[Porsche] number:202 config reset OK");
}

// ── boolean:200 "change" event — правилният Virtual API ──────────────────────

if (vClimate) {
  vClimate.on("change", function(ev) {
    if (_climateUpdating) {
      print("[Porsche] Skip script-triggered climate change");
      return;
    }
    print("[Porsche] User toggled climate → " + ev.value);
    if (ev.value === true) { startClimate(); }
    else                   { stopClimate();  }
  });
} else {
  print("[Porsche] WARNING: boolean:200 (Climate) not found!");
}

// ── Force server refresh, после poll ─────────────────────────────────────────

function refreshAndPoll() {
  apiPost("/refresh", function(data) {
    if (data) { print("[Porsche] Cache refreshed: " + data.last_updated); }
    pollVehicle();
  });
}

// ── Периодичен polling ────────────────────────────────────────────────────────

Timer.set(POLL_SEC * 1000, true, function() { pollVehicle(); });
Timer.set(3000, false, function() { pollVehicle(); });

print("[Porsche] Script started. VIN=" + VIN + " poll=" + POLL_SEC + "s");

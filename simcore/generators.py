from __future__ import annotations

import json
import random
import re
import struct
import time
from datetime import datetime, timezone


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def enocean_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _estr(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _device_id(cfg: dict) -> str:
    return cfg.get("address") or cfg.get("id") or cfg.get("name") or ""


def _csv_cell(v) -> str:
    s = _text_val(v)
    if any(ch in s for ch in (",", '"', "\n")):
        return '"' + s.replace('"', '""') + '"'
    return s


def default_point() -> dict:
    return {"name": "value", "datatype": "float", "sim": "walk",
            "min": 0, "max": 100, "step": 2.5, "unit": "", "decimals": 2,
            "values": [], "init": None}


DATATYPE_DEFAULTS = {
    "float":  {"min": 0, "max": 100, "step": 2.5,  "decimals": 2, "values": []},
    "int":    {"min": 0, "max": 100, "step": 2,    "decimals": 0, "values": []},
    "bool":   {"min": 0, "max": 1,   "step": 0.05, "decimals": 0, "values": []},
    "enum":   {"min": 0, "max": 1,   "step": 0.05, "decimals": 0, "values": ["ON", "OFF"]},
    "string": {"min": 0, "max": 1,   "step": 0.05, "decimals": 0, "values": ["OK"]},
}


DATATYPE_FIELDS = {
    "float":  ["min", "max", "step", "unit", "decimals"],
    "int":    ["min", "max", "step", "unit"],
    "bool":   ["step"],
    "enum":   ["step", "values"],
    "string": ["step", "values"],
}


def _prob(step, default=0.05) -> float:
    try:
        s = float(step)
    except (TypeError, ValueError):
        return default
    return s if 0 < s <= 1 else default


def _coerce(v, datatype):
    if v is None:
        return None
    if datatype == "bool":
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "on", "yes")
        return bool(v)
    if datatype == "int":
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
    if datatype == "float":
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return str(v)


def _text_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def _p(name, datatype, sim, mn, mx, step, unit="", decimals=2, values=None):
    return {"name": name, "datatype": datatype, "sim": sim, "min": mn, "max": mx,
            "step": step, "unit": unit, "decimals": decimals,
            "values": values or [], "init": None}


PRESETS = {
    "gateway": {
        "status": {"label": "Gateway status / heartbeat", "points": [
            _p("cpu", "float", "walk", 4, 72, 3, "%"),
            _p("mem", "float", "walk", 30, 82, 2, "%"),
            _p("signal_strength", "int", "walk", -78, -42, 3, "dBm"),
            _p("uptime", "int", "increment", 0, 999999999, 1, "s"),
            _p("online", "bool", "fixed", 0, 1, 0.05),
        ]},
        "edge_gateway": {"label": "Edge gateway (full diagnostics)", "points": [
            _p("cpu", "float", "walk", 4, 72, 3, "%", 1),
            _p("mem", "float", "walk", 30, 82, 2, "%", 1),
            _p("disk", "float", "walk", 20, 90, 0.5, "%", 1),
            _p("temperature", "float", "walk", 32, 68, 0.8, "°C", 1),
            _p("rssi", "int", "walk", -85, -45, 3, "dBm"),
            _p("connected_devices", "int", "walk", 1, 32, 1),
            _p("tx_messages", "int", "increment", 0, 999999999, 4),
            _p("uptime", "int", "increment", 0, 999999999, 1, "s"),
            _p("online", "bool", "fixed", 0, 1, 0.05),
        ]},
    },


    "enocean": {
        "A5-04-01": {"label": "Temp + Humidity (A5-04-01)", "points": [
            _p("HUM", "float", "walk", 45, 75, 0.6, "%", 1),
            _p("TMP", "float", "walk", 20, 30, 0.15, "°C", 1),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
            _p("TSN", "string", "fixed", 0, 0, 0, values=["available"]),
        ]},
        "A5-09-04": {"label": "CO2 + Temp + Humidity (A5-09-04)", "points": [
            _p("HUM", "float", "walk", 45, 70, 0.6, "%", 1),
            _p("Conc", "float", "walk", 400, 1200, 18, "ppm", 1),
            _p("TMP", "float", "walk", 20, 30, 0.15, "°C", 1),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
            _p("HSN", "string", "fixed", 0, 0, 0, values=["Humidity Sensor avaialbe"]),
            _p("TSN", "string", "fixed", 0, 0, 0, values=["Temperature Sensor avaialbe"]),
        ]},
        "A5-07-03": {"label": "Occupancy + Illumination + Supply V (A5-07-03)", "points": [
            _p("SVC", "float", "walk", 2.8, 3.3, 0.03, "V", 1),
            _p("ILL", "float", "walk", 0, 400, 20, "lx", 1),
            _p("PIRS", "enum", "walk", 0, 1, 0.05,
               values=["Motion detected", "Uncertain of occupancy status"]),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
        "A5-02-05": {"label": "Temperature 0..40°C (A5-02-05)", "points": [
            _p("TMP", "float", "walk", 20, 30, 0.15, "°C", 1),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
        "A5-06-02": {"label": "Light 0..1024 lx (A5-06-02)", "points": [
            _p("ILL", "float", "walk", 0, 1024, 25, "lx", 1),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
        "A5-12-01": {"label": "Energy meter (A5-12-01)", "points": [
            _p("power", "float", "walk", 40, 2400, 60, "W", 1),
            _p("energy", "float", "increment", 0, 999999999, 0.02, "kWh", 2),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
        "D5-00-01": {"label": "Contact / door-window (D5-00-01)", "points": [
            _p("contact", "enum", "walk", 0, 1, 0.03, values=["open", "closed"]),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
        "F6-02-01": {"label": "Rocker switch 2ch (F6-02-01)", "points": [
            _p("button_a", "enum", "walk", 0, 1, 0.03, values=["pressed", "released"]),
            _p("button_b", "enum", "walk", 0, 1, 0.03, values=["pressed", "released"]),
            _p("LRNB", "string", "fixed", 0, 0, 0, values=["Data telegram"]),
        ]},
    },
    "zigbee": {
        "temp_humidity": {"label": "Temp/Humidity sensor", "points": [
            _p("temperature", "float", "walk", 15, 32, 0.15, "°C"),
            _p("humidity", "float", "walk", 25, 75, 0.6, "%"),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "door_sensor": {"label": "Door/Window sensor", "points": [
            _p("contact", "bool", "walk", 0, 1, 0.03),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "motion": {"label": "Motion sensor (PIR)", "points": [
            _p("occupancy", "bool", "walk", 0, 1, 0.05),
            _p("illuminance", "int", "walk", 0, 1200, 30, "lx"),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
        ]},
        "smart_plug": {"label": "Smart plug (metering)", "points": [
            _p("state", "enum", "walk", 0, 1, 0.02, values=["ON", "OFF"]),
            _p("voltage", "float", "walk", 224, 244, 0.8, "V"),
            _p("current", "float", "walk", 0.1, 9.5, 0.3, "A"),
            _p("power", "float", "walk", 20, 2200, 55, "W"),
            _p("energy", "float", "increment", 0, 999999, 0.01, "kWh"),
        ]},
        "vibration": {"label": "Vibration sensor", "points": [
            _p("vibration", "bool", "walk", 0, 1, 0.04),
            _p("angle_x", "float", "walk", -90, 90, 4, "°"),
            _p("angle_y", "float", "walk", -90, 90, 4, "°"),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
        ]},
        "air_quality": {"label": "Air quality sensor", "points": [
            _p("pm25", "float", "walk", 5, 90, 2.5, "µg/m³", 1),
            _p("voc_index", "int", "walk", 50, 400, 12),
            _p("co2", "int", "walk", 400, 1600, 20, "ppm"),
            _p("temperature", "float", "walk", 18, 30, 0.15, "°C", 1),
            _p("humidity", "float", "walk", 30, 70, 0.6, "%", 1),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "water_leak": {"label": "Water leak sensor", "points": [
            _p("water_leak", "bool", "walk", 0, 1, 0.01),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "thermostat": {"label": "Thermostat / TRV", "points": [
            _p("local_temperature", "float", "walk", 17, 26, 0.12, "°C", 1),
            _p("occupied_heating_setpoint", "float", "fixed", 18, 24, 0, "°C", 1),
            _p("system_mode", "enum", "walk", 0, 1, 0.02, values=["heat", "off", "auto"]),
            _p("running_state", "enum", "walk", 0, 1, 0.05, values=["idle", "heat"]),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "light_dimmer": {"label": "Dimmable light", "points": [
            _p("state", "enum", "walk", 0, 1, 0.03, values=["ON", "OFF"]),
            _p("brightness", "int", "walk", 0, 254, 12),
            _p("color_temp", "int", "walk", 150, 500, 10),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
        "button": {"label": "Wireless button / scene switch", "points": [
            _p("action", "enum", "walk", 0, 1, 0.04,
               values=["single", "double", "hold", "release"]),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
            _p("linkquality", "int", "walk", 40, 255, 6),
        ]},
    },
    "modbus": {
        "energy_meter_3p": {"label": "3-phase energy meter", "points": [
            _p("voltage_l1", "float", "walk", 226, 242, 0.9, "V"),
            _p("voltage_l2", "float", "walk", 226, 242, 0.9, "V"),
            _p("voltage_l3", "float", "walk", 226, 242, 0.9, "V"),
            _p("current_l1", "float", "walk", 1, 40, 1.4, "A"),
            _p("current_l2", "float", "walk", 1, 40, 1.4, "A"),
            _p("current_l3", "float", "walk", 1, 40, 1.4, "A"),
            _p("power_total", "float", "walk", 500, 26000, 700, "W"),
            _p("pf", "float", "walk", 0.82, 0.99, 0.01),
            _p("frequency", "float", "walk", 49.8, 50.2, 0.03, "Hz"),
            _p("energy_import", "float", "increment", 0, 999999999, 0.4, "kWh"),
        ]},
        "vfd": {"label": "VFD / motor drive", "points": [
            _p("speed", "int", "walk", 0, 1480, 40, "rpm"),
            _p("output_freq", "float", "walk", 0, 50, 1.4, "Hz"),
            _p("motor_current", "float", "walk", 2, 55, 2.2, "A"),
            _p("dc_bus_voltage", "float", "walk", 530, 580, 4, "V"),
            _p("heatsink_temp", "float", "walk", 30, 78, 1.1, "°C"),
            _p("run_status", "enum", "walk", 0, 1, 0.02, values=["RUN", "STOP", "FAULT"]),
        ]},
        "pressure_flow": {"label": "Pressure + flow transmitter", "points": [
            _p("pressure", "float", "walk", 1.2, 8.5, 0.2, "bar"),
            _p("flow", "float", "walk", 5, 120, 4, "m³/h"),
            _p("totalizer", "float", "increment", 0, 999999999, 1.5, "m³"),
        ]},
        "dg_set": {"label": "Diesel generator (DG set)", "points": [
            _p("output_power", "float", "walk", 0, 500, 25, "kW", 1),
            _p("fuel_level", "float", "walk", 15, 100, 0.3, "%", 1),
            _p("battery_voltage", "float", "walk", 23.5, 28, 0.15, "V", 1),
            _p("coolant_temperature", "float", "walk", 60, 98, 1, "°C", 1),
            _p("oil_pressure", "float", "walk", 2.5, 5.5, 0.12, "bar", 2),
            _p("engine_rpm", "int", "walk", 0, 1500, 30, "rpm"),
            _p("run_hours", "float", "increment", 0, 999999, 0.01, "h", 2),
            _p("running", "bool", "walk", 0, 1, 0.02),
        ]},
        "transformer": {"label": "Distribution transformer", "points": [
            _p("oil_temperature", "float", "walk", 45, 92, 0.7, "°C", 1),
            _p("winding_temperature", "float", "walk", 55, 105, 0.9, "°C", 1),
            _p("load_current", "float", "walk", 20, 380, 12, "A", 1),
            _p("voltage", "float", "walk", 10800, 11200, 40, "V", 1),
            _p("oil_level", "float", "walk", 55, 95, 0.4, "%", 1),
            _p("tap_position", "int", "walk", 1, 9, 1),
        ]},
        "chiller": {"label": "Chiller (HVAC)", "points": [
            _p("chw_supply_temp", "float", "walk", 5.5, 9, 0.15, "°C", 1),
            _p("chw_return_temp", "float", "walk", 10, 14.5, 0.18, "°C", 1),
            _p("condenser_temp", "float", "walk", 30, 45, 0.4, "°C", 1),
            _p("compressor_current", "float", "walk", 40, 220, 8, "A", 1),
            _p("cop", "float", "walk", 2.8, 6.2, 0.08, "", 2),
            _p("capacity", "float", "walk", 20, 100, 3, "%", 1),
            _p("running", "bool", "walk", 0, 1, 0.02),
        ]},
        "ahu": {"label": "Air handling unit (AHU)", "points": [
            _p("supply_air_temp", "float", "walk", 12, 22, 0.2, "°C", 1),
            _p("return_air_temp", "float", "walk", 20, 28, 0.2, "°C", 1),
            _p("setpoint", "float", "fixed", 16, 24, 0, "°C", 1),
            _p("fan_speed", "float", "walk", 30, 100, 2.5, "%", 1),
            _p("damper_position", "float", "walk", 0, 100, 3, "%", 1),
            _p("filter_dp", "float", "walk", 50, 320, 4, "Pa", 1),
            _p("fan_on", "bool", "walk", 0, 1, 0.02),
        ]},
        "air_compressor": {"label": "Air compressor", "points": [
            _p("discharge_pressure", "float", "walk", 5.5, 9, 0.15, "bar", 2),
            _p("discharge_temperature", "float", "walk", 60, 105, 1.2, "°C", 1),
            _p("motor_current", "float", "walk", 15, 90, 4, "A", 1),
            _p("run_hours", "float", "increment", 0, 999999, 0.01, "h", 2),
            _p("loaded", "bool", "walk", 0, 1, 0.06),
        ]},
        "solar_inverter": {"label": "Solar PV inverter", "points": [
            _p("dc_voltage", "float", "walk", 480, 780, 8, "V", 1),
            _p("dc_current", "float", "walk", 0, 22, 1.2, "A", 2),
            _p("ac_power", "float", "walk", 0, 50, 2.5, "kW", 2),
            _p("daily_yield", "float", "increment", 0, 999999, 0.02, "kWh", 2),
            _p("total_yield", "float", "increment", 0, 999999999, 0.02, "kWh", 1),
            _p("efficiency", "float", "walk", 92, 98.5, 0.2, "%", 2),
            _p("grid_frequency", "float", "walk", 49.8, 50.2, 0.03, "Hz", 2),
            _p("inverter_temperature", "float", "walk", 28, 68, 0.8, "°C", 1),
        ]},
        "ups": {"label": "UPS / battery backup", "points": [
            _p("input_voltage", "float", "walk", 205, 245, 2, "V", 1),
            _p("output_voltage", "float", "walk", 228, 232, 0.4, "V", 1),
            _p("battery_voltage", "float", "walk", 46, 54, 0.3, "V", 1),
            _p("battery_charge", "float", "walk", 40, 100, 0.5, "%", 1),
            _p("load", "float", "walk", 10, 85, 2.5, "%", 1),
            _p("runtime_remaining", "int", "walk", 5, 120, 2, "min"),
            _p("on_battery", "bool", "walk", 0, 1, 0.01),
        ]},
        "pump": {"label": "Centrifugal pump", "points": [
            _p("suction_pressure", "float", "walk", 0.5, 2.5, 0.08, "bar", 2),
            _p("discharge_pressure", "float", "walk", 3, 9, 0.2, "bar", 2),
            _p("flow", "float", "walk", 10, 180, 6, "m³/h", 1),
            _p("motor_current", "float", "walk", 5, 60, 2.5, "A", 1),
            _p("seal_temperature", "float", "walk", 30, 78, 0.9, "°C", 1),
            _p("running", "bool", "walk", 0, 1, 0.02),
        ]},
        "boiler": {"label": "Steam boiler", "points": [
            _p("steam_pressure", "float", "walk", 4, 12, 0.2, "bar", 2),
            _p("steam_temperature", "float", "walk", 150, 195, 1.5, "°C", 1),
            _p("feedwater_temperature", "float", "walk", 60, 105, 1, "°C", 1),
            _p("fuel_flow", "float", "walk", 5, 60, 2, "m³/h", 2),
            _p("efficiency", "float", "walk", 78, 94, 0.4, "%", 1),
            _p("burner_on", "bool", "walk", 0, 1, 0.05),
        ]},
    },


    "direct": {
        "blank": {"label": "Blank (define your own points)", "points": []},
        "env_sensor": {"label": "Environment sensor (temp / humidity / pressure)", "points": [
            _p("temperature", "float", "walk", 18, 28, 0.15, "°C", 1),
            _p("humidity", "float", "walk", 30, 70, 0.6, "%", 1),
            _p("pressure", "float", "walk", 980, 1020, 0.8, "hPa", 1),
        ]},
        "air_quality": {"label": "Air quality (PM2.5 / CO₂ / TVOC)", "points": [
            _p("pm25", "float", "walk", 5, 90, 2.5, "µg/m³", 1),
            _p("pm10", "float", "walk", 8, 140, 4, "µg/m³", 1),
            _p("co2", "int", "walk", 400, 1600, 20, "ppm"),
            _p("tvoc", "int", "walk", 50, 800, 15, "ppb"),
            _p("temperature", "float", "walk", 18, 30, 0.15, "°C", 1),
            _p("humidity", "float", "walk", 30, 70, 0.6, "%", 1),
        ]},
        "power_meter": {"label": "Single-phase power meter", "points": [
            _p("voltage", "float", "walk", 228, 242, 0.8, "V", 1),
            _p("current", "float", "walk", 0.5, 32, 1.2, "A", 2),
            _p("power", "float", "walk", 100, 7200, 180, "W", 1),
            _p("power_factor", "float", "walk", 0.82, 0.99, 0.01, "", 2),
            _p("frequency", "float", "walk", 49.8, 50.2, 0.03, "Hz", 2),
            _p("energy", "float", "increment", 0, 999999999, 0.05, "kWh", 2),
        ]},
        "water_meter": {"label": "Water meter (flow / totalizer)", "points": [
            _p("flow_rate", "float", "walk", 0, 45, 1.5, "m³/h", 2),
            _p("total_volume", "float", "increment", 0, 999999999, 0.02, "m³", 2),
            _p("pressure", "float", "walk", 1.5, 6, 0.15, "bar", 2),
            _p("temperature", "float", "walk", 12, 28, 0.2, "°C", 1),
            _p("leak_detected", "bool", "walk", 0, 1, 0.01),
        ]},
        "tank_level": {"label": "Tank level / volume", "points": [
            _p("level", "float", "walk", 10, 95, 1.2, "%", 1),
            _p("volume", "float", "walk", 200, 9500, 120, "L", 1),
            _p("temperature", "float", "walk", 15, 35, 0.2, "°C", 1),
            _p("high_level_alarm", "bool", "walk", 0, 1, 0.01),
        ]},
        "gps_tracker": {"label": "GPS asset tracker", "points": [
            _p("latitude", "float", "walk", 12.90, 13.10, 0.0008, "°", 6),
            _p("longitude", "float", "walk", 77.50, 77.70, 0.0008, "°", 6),
            _p("speed", "float", "walk", 0, 80, 4, "km/h", 1),
            _p("heading", "int", "walk", 0, 359, 8, "°"),
            _p("altitude", "float", "walk", 850, 950, 2, "m", 1),
            _p("satellites", "int", "walk", 4, 12, 1),
            _p("ignition", "bool", "walk", 0, 1, 0.02),
        ]},
        "weather_station": {"label": "Weather station", "points": [
            _p("temperature", "float", "walk", 8, 38, 0.3, "°C", 1),
            _p("humidity", "float", "walk", 25, 95, 0.8, "%", 1),
            _p("pressure", "float", "walk", 990, 1025, 0.5, "hPa", 1),
            _p("wind_speed", "float", "walk", 0, 45, 2, "km/h", 1),
            _p("wind_direction", "int", "walk", 0, 359, 15, "°"),
            _p("rainfall", "float", "increment", 0, 999999, 0.02, "mm", 2),
            _p("solar_radiation", "float", "walk", 0, 1000, 40, "W/m²", 1),
        ]},
        "soil_moisture": {"label": "Soil / agriculture probe", "points": [
            _p("soil_moisture", "float", "walk", 12, 55, 0.8, "%", 1),
            _p("soil_temperature", "float", "walk", 14, 34, 0.2, "°C", 1),
            _p("ec", "float", "walk", 0.2, 3.2, 0.06, "mS/cm", 2),
            _p("ph", "float", "walk", 5.5, 8, 0.04, "pH", 2),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
        ]},
        "cold_chain": {"label": "Cold-chain / refrigeration monitor", "points": [
            _p("temperature", "float", "walk", -22, 6, 0.3, "°C", 1),
            _p("humidity", "float", "walk", 55, 92, 0.8, "%", 1),
            _p("door_open", "bool", "walk", 0, 1, 0.02),
            _p("compressor_on", "bool", "walk", 0, 1, 0.05),
            _p("battery", "int", "walk", 20, 100, 1, "%"),
        ]},
        "motor_pdm": {"label": "Motor condition / predictive maintenance", "points": [
            _p("rms_velocity", "float", "walk", 0.4, 11, 0.3, "mm/s", 2),
            _p("peak_acceleration", "float", "walk", 0.2, 8, 0.25, "g", 2),
            _p("bearing_temperature", "float", "walk", 32, 95, 0.9, "°C", 1),
            _p("rpm", "int", "walk", 0, 1480, 40, "rpm"),
            _p("running", "bool", "walk", 0, 1, 0.02),
        ]},
    },
}

KIND_LABELS = {
    "gateway": "Gateway",
    "direct": "Direct MQTT",
    "zigbee": "Zigbee",
    "enocean": "EnOcean (EEP)",
    "modbus": "Modbus",
}

DATATYPES = ["float", "int", "bool", "string", "enum"]
SIM_MODES = ["walk", "increment", "fixed"]


def catalog() -> dict:
    return {
        "kinds": KIND_LABELS,
        "datatypes": DATATYPES,
        "sim_modes": SIM_MODES,
        "datatype_defaults": DATATYPE_DEFAULTS,
        "datatype_fields": DATATYPE_FIELDS,
        "presets": {
            kind: {pk: {"label": pv["label"], "points": pv["points"]}
                   for pk, pv in presets.items()}
            for kind, presets in PRESETS.items()
        },
    }


class SimPoint:
    def __init__(self, spec: dict):
        self.spec = {**default_point(), **spec}
        s = self.spec
        self.hold = 0
        dt = s["datatype"]
        init = _coerce(s["init"], dt) if s["init"] is not None else None
        if init is not None:
            self.value = init
        elif s["sim"] == "increment":
            self.value = int(s["min"]) if dt == "int" else round(float(s["min"]), int(s["decimals"]))
        elif dt == "bool":
            self.value = random.choice([True, False])
        elif dt == "enum":
            self.value = random.choice(s["values"]) if s["values"] else ""
        elif dt == "string":
            self.value = s["values"][0] if s["values"] else ""
        elif dt == "int":
            self.value = random.randint(int(s["min"]), int(max(s["min"], s["max"])))
        else:
            self.value = round(random.uniform(float(s["min"]), float(s["max"])),
                               int(s["decimals"]))

    def tick(self):
        if self.hold > 0:
            self.hold -= 1
            return
        s = self.spec
        dt, sim = s["datatype"], s["sim"]
        if sim == "fixed":
            return
        if dt == "bool":


            if random.random() < _prob(s["step"]):
                self.value = not bool(self.value)
        elif dt == "enum":
            if s["values"] and random.random() < _prob(s["step"]):
                self.value = random.choice(s["values"])
        elif dt == "string":
            if sim == "walk" and s["values"] and random.random() < _prob(s["step"]):
                self.value = random.choice(s["values"])
        elif dt == "int":
            if sim == "increment":
                self.value = int(self.value) + abs(int(round(random.uniform(0, float(s["step"])))))
            else:
                v = int(self.value) + random.randint(-int(abs(s["step"])) or -1,
                                                     int(abs(s["step"])) or 1)
                self.value = int(min(int(s["max"]), max(int(s["min"]), v)))
        else:
            if sim == "increment":
                self.value = round(float(self.value) + abs(random.uniform(0, float(s["step"]))),
                                   int(s["decimals"]))
            else:
                v = float(self.value) + random.uniform(-float(s["step"]), float(s["step"]))
                self.value = round(min(float(s["max"]), max(float(s["min"]), v)),
                                   int(s["decimals"]))

    def set(self, v):

        cv = _coerce(v, self.spec["datatype"])
        if cv is None:
            return False
        self.value = cv
        self.hold = 5
        return True


def _enocean_raw(preset: str, vals: dict, sender_id: str) -> str:
    rorg = {"A5": 0xA5, "D5": 0xD5, "F6": 0xF6}.get((preset or "A5")[:2], 0xA5)
    def num(k, d=0):
        v = vals.get(k, d)
        return float(v) if isinstance(v, (int, float)) else d
    if rorg == 0xA5:
        if "temperature" in vals and "humidity" in vals:
            data = [0x00, int(num("humidity") / 100 * 250) & 0xFF,
                    int(num("temperature") / 40 * 250) & 0xFF, 0x0A]
        elif "co2" in vals:
            data = [int(num("humidity") / 100 * 200) & 0xFF,
                    int(num("co2") / 2550 * 255) & 0xFF,
                    int(num("temperature", 20) / 51 * 255) & 0xFF, 0x0E]
        elif "power" in vals:
            mr = int(num("power"))
            data = [(mr >> 16) & 0xFF, (mr >> 8) & 0xFF, mr & 0xFF, 0x1C]
        elif "illuminance" in vals:
            data = [0x00, int(num("illuminance") / 1024 * 255) & 0xFF, 0x00, 0x0A]
        elif "temperature" in vals:
            data = [0x00, 0x00, int(255 - num("temperature") / 40 * 255) & 0xFF, 0x0A]
        else:
            data = [0x00, 0x00, int(num(next(iter(vals), ""), 0)) & 0xFF, 0x0A]
    elif rorg == 0xD5:
        data = [0x09 if vals.get("contact") else 0x08]
    else:
        data = [0x30 if vals.get("button_a") else (0x70 if vals.get("button_b") else 0x00)]
    sid = (sender_id or "0414404C").replace(" ", "")
    try:
        sidb = [int(sid[i:i + 2], 16) for i in range(0, 8, 2)]
    except ValueError:
        sidb = [0x04, 0x14, 0x40, 0x4C]
    return "".join(f"{b:02X}" for b in [rorg] + data + sidb + [0x00])


_HARVEST_CAP = ["very good", "very good", "good", "medium"]


def _enocean_sensor_block(ch: "DeviceRuntime", gw_id: str) -> dict:
    dev_id = ch.cfg.get("address") or ch.cfg.get("id") or ch.cfg.get("name") or ""
    eep = ch.cfg.get("preset", "") or ""
    ts = enocean_now()
    rssi = str(-random.randint(45, 92))
    meta = {"gw_id": gw_id, "deviceId": dev_id, "time": ts, "rssi": rssi}
    data0 = {"gw_id": gw_id, "deviceId": dev_id, "time": ts, "eep": eep, "rssi": rssi}
    for k, v in ch.values().items():
        data0[k] = _estr(v)
    energy = str(random.choice([0, 0, 0, 0, 99]))
    return {
        "data0": data0,
        "data1": {**meta, "mid": "0x06:Energy status of device", "ENERGY": energy},
        "data2": {**meta, "mid": "0x0D:Current delivery of the harvester",
                  "Capabilities": random.choice(_HARVEST_CAP)},
        "data3": {**meta, "mid": "0x10:Backup Battery Status", "ENERGY": "0"},
    }


class DeviceRuntime:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.points: dict[str, SimPoint] = {}
        self.seq = 0
        self.rebuild(cfg)

    def rebuild(self, cfg: dict):
        self.cfg = cfg
        old = self.points
        self.points = {}
        for spec in cfg.get("points", []):
            name = spec.get("name") or "point"
            sp = SimPoint(spec)
            if name in old and old[name].spec["datatype"] == sp.spec["datatype"]:
                sp.value = old[name].value
            self.points[name] = sp

    def tick(self):
        for p in self.points.values():
            p.tick()

    def values(self) -> dict:


        return {n: p.value for n, p in self.points.items()}

    def display_values(self) -> dict:
        return {n: p.value for n, p in self.points.items()}

    def set_value(self, name, value) -> bool:
        return self.points[name].set(value) if name in self.points else False


    def render(self, binding: dict, children: list["DeviceRuntime"]) -> str:
        fmt = binding["data_format"]
        self.seq += 1
        vals = self.values()

        if fmt == "raw" or (binding.get("pub_type") == "raw" and self.cfg["kind"] == "enocean"):
            return json.dumps({
                "gatewayId": self.cfg.get("address") or "0414404C",
                "deviceName": self.cfg["name"],
                "eep": self.cfg.get("preset", ""),
                "telegram": _enocean_raw(self.cfg.get("preset", ""), vals,
                                         self.cfg.get("address", "")),
                "rssi": -random.randint(45, 82),
                "timestamp": utc_iso(),
            })

        if fmt == "enocean_gw":


            gw_id = self.cfg.get("address") or self.cfg.get("id") or self.cfg.get("name") or "GW"
            sensors = list(children) if children else [self]
            random.shuffle(sensors)
            return json.dumps({f"sensor{i}": _enocean_sensor_block(ch, gw_id)
                               for i, ch in enumerate(sensors)})

        if fmt == "array":


            items = list(children) if children else [self]
            random.shuffle(items)
            devices = []
            for ch in items:
                item = {"deviceId": ch.cfg.get("address") or ch.cfg.get("id") or ch.cfg["name"],
                        "deviceType": ch.cfg["kind"]}
                if ch.cfg["kind"] == "enocean" and ch.cfg.get("preset"):
                    item["eep"] = ch.cfg["preset"]
                item.update(ch.values())
                devices.append(item)
            return json.dumps({"devices": devices, "ts": utc_iso()})

        if fmt == "zigbee2mqtt":


            if children:
                return json.dumps({ch.cfg["name"]: ch.values() for ch in children})
            return json.dumps(vals)

        if fmt == "template" and binding.get("template"):
            return self._render_template(binding["template"], vals)

        if fmt == "kv":
            return ",".join(f"{k}={_text_val(v)}" for k, v in vals.items())

        if fmt == "status":
            return json.dumps({"online": True, "timestamp": utc_iso()})

        if fmt == "opcua":
            now_ms = int(time.time() * 1000)
            rows = [{"server_name": self.cfg["name"], "node_id": f"ns=2;s={k}",
                     "display_name": k, "value": v, "quality": "Good",
                     "timestamp": now_ms} for k, v in vals.items()]
            for ch in children:
                rows += [{"server_name": self.cfg["name"],
                          "node_id": f"ns=2;s={ch.cfg['name']}.{k}",
                          "display_name": f"{ch.cfg['name']}.{k}", "value": v,
                          "quality": "Good", "timestamp": now_ms}
                         for k, v in ch.values().items()]
            return json.dumps(rows)

        if fmt == "tag_array":


            d = [{"tag": k, "value": v} for k, v in vals.items()]
            for ch in children:
                for k, v in ch.values().items():
                    d.append({"tag": f"{ch.cfg['name']}.{k}", "value": v})
            return json.dumps({"d": d, "ts": utc_iso()})

        if fmt == "csv":


            cells = [_csv_cell(v) for v in vals.values()]
            for ch in children:
                cells += [_csv_cell(v) for v in ch.values().values()]
            return ",".join(cells)

        if fmt == "raw_number":


            for v in vals.values():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return str(v)
            return "0"

        if fmt == "binary":


            ordered = list(vals.values())
            for ch in children:
                ordered += list(ch.values().values())
            parts = []
            for v in ordered:
                f = float(v) if isinstance(v, (int, float, bool)) else 0.0
                parts.append(struct.pack(">f", f).hex().upper())
            return "".join(parts)

        if fmt == "flat":


            out = dict(vals)
            for ch in children:
                out[ch.cfg["name"]] = ch.values()
            out["timestamp"] = utc_iso()
            out["quality"] = "good"
            return json.dumps(out)


        payload = {
            "deviceId": _device_id(self.cfg),
            "deviceName": self.cfg["name"],
            "deviceType": self.cfg["kind"],
            "address": self.cfg.get("address", ""),
            "seq": self.seq,
            "timestamp": utc_iso(),
            "data": vals,
        }
        if self.cfg["kind"] == "enocean":
            payload["eep"] = self.cfg.get("preset", "")
            payload["rssi"] = -random.randint(45, 82)
        if children:
            payload["subDevices"] = [
                {"deviceId": _device_id(ch.cfg),
                 "deviceName": ch.cfg["name"], "deviceType": ch.cfg["kind"],
                 "address": ch.cfg.get("address", ""),
                 **({"eep": ch.cfg.get("preset", "")} if ch.cfg["kind"] == "enocean" else {}),
                 "data": ch.values()}
                for ch in children
            ]
        return json.dumps(payload)

    def _render_template(self, template: str, vals: dict) -> str:
        out = template
        out = out.replace("{{ts}}", utc_iso())
        out = out.replace("{{epoch}}", str(int(time.time())))
        out = out.replace("{{epoch_ms}}", str(int(time.time() * 1000)))
        out = out.replace("{{name}}", self.cfg["name"])
        out = out.replace("{{seq}}", str(self.seq))
        for k, v in vals.items():
            out = out.replace("{{" + k + "}}",
                              json.dumps(v) if isinstance(v, str) else str(v))
        out = re.sub(r"\{\{rand\(([-\d.]+),([-\d.]+)\)\}\}",
                     lambda m: str(round(random.uniform(float(m.group(1)), float(m.group(2))), 2)), out)
        out = re.sub(r"\{\{randint\((\d+),(\d+)\)\}\}",
                     lambda m: str(random.randint(int(m.group(1)), int(m.group(2)))), out)
        out = out.replace("{{bool}}", random.choice(["true", "false"]))
        return out

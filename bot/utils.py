import urllib.parse

import httpx

WMO = {0: "Clear ☀️", 1: "Mostly clear 🌤", 2: "Partly cloudy ⛅", 3: "Overcast ☁️", 45: "Fog 🌫", 48: "Fog 🌫",
       51: "Light drizzle 🌦", 53: "Drizzle 🌦", 55: "Heavy drizzle 🌧", 61: "Light rain 🌦", 63: "Rain 🌧", 65: "Heavy rain 🌧",
       71: "Light snow 🌨", 73: "Snow 🌨", 75: "Heavy snow ❄️", 80: "Showers 🌦", 81: "Showers 🌧", 82: "Heavy showers ⛈",
       95: "Thunderstorm ⛈", 96: "Thunderstorm, hail ⛈", 99: "Thunderstorm, hail ⛈"}


def weather(city):
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}, timeout=15).json()
    res = (g.get("results") or [None])[0]
    if not res:
        return None
    f = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": res["latitude"], "longitude": res["longitude"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}).json()
    cur, d = f["current"], f["daily"]
    lines = [f"🌍 {res['name']}, {res.get('country', '')}",
             f"Now: {WMO.get(cur['weather_code'], '—')} · {cur['temperature_2m']}°C · humidity {cur['relative_humidity_2m']}% · wind {cur['wind_speed_10m']} km/h", ""]
    for i in range(len(d["time"])):
        lines.append(f"{d['time'][i][5:]}: {WMO.get(d['weather_code'][i], '—')} · {d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C · rain {d['precipitation_probability_max'][i]}%")
    return "\n".join(lines)


def convert(amount, src, dst):
    r = httpx.get(f"https://open.er-api.com/v6/latest/{urllib.parse.quote(src.upper())}", timeout=15).json()
    rate = (r.get("rates") or {}).get(dst.upper())
    if r.get("result") != "success" or not rate:
        return None
    return amount * rate, rate


def qr_png(text):
    r = httpx.get("https://api.qrserver.com/v1/create-qr-code/", timeout=20,
                  params={"size": "500x500", "data": text[:900], "margin": 10})
    if r.status_code != 200:
        return None
    return r.content

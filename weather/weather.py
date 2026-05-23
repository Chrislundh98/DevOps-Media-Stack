#!/usr/bin/env python3
"""
Weather digest for Jönköping, Sweden.
Blends Open-Meteo (multi-model ECMWF/GFS) + SMHI data.

Schedule:
  - Daily digest at 08:00 every day (includes late-night slots for pub-goers)
  - Weekly overview at 08:00 every Monday (sends in addition to daily)
"""

import os
import logging
import time
from collections import Counter
from datetime import datetime, timedelta

import pytz
import requests
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("weather")

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK"]
LAT = 57.7826
LON = 14.1618
TZ  = pytz.timezone("Europe/Stockholm")

# (hour, day_offset): 0=today, 1=tomorrow
# 22:00, 00:00, 02:00 cover late nights / early morning home from the pub
FORECAST_SLOTS = [
    (8, 0), (10, 0), (12, 0), (14, 0), (16, 0), (18, 0), (20, 0), (22, 0),
    (0, 1), (2, 1),
]

WMO = {
    0:  ("☀️",  "Clear sky"),
    1:  ("🌤️", "Mainly clear"),
    2:  ("⛅",  "Partly cloudy"),
    3:  ("☁️",  "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Icy fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"),
    63: ("🌧️", "Moderate rain"),
    65: ("🌧️", "Heavy rain"),
    71: ("🌨️", "Light snow"),
    73: ("🌨️", "Moderate snow"),
    75: ("❄️",  "Heavy snow"),
    77: ("🌨️", "Snow grains"),
    80: ("🌦️", "Light showers"),
    81: ("🌧️", "Showers"),
    82: ("⛈️",  "Heavy showers"),
    85: ("🌨️", "Snow showers"),
    86: ("❄️",  "Heavy snow showers"),
    95: ("⛈️",  "Thunderstorm"),
    96: ("⛈️",  "Thunderstorm + hail"),
    99: ("⛈️",  "Thunderstorm + heavy hail"),
}

WIND_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# ─── Helpers ────────────────────────────────────────────────────────────────

def wind_dir(degrees: float) -> str:
    return WIND_DIRS[round(degrees / 45) % 8]

def fmt_temp(t: float) -> str:
    return f"{int(round(t))}°"

def is_snow(c: int) -> bool:
    return c in (71, 73, 75, 77, 85, 86)

def is_rain(c: int) -> bool:
    return 51 <= c <= 82

def dominant_code(codes: list[int]) -> int:
    """Pick most representative weather code: precip wins if it appears ≥2h."""
    if not codes:
        return 0
    counts = Counter(codes)
    precip = [c for c, n in counts.items() if c >= 51 and n >= 2]
    if precip:
        return max(precip)
    return counts.most_common(1)[0][0]

# ─── Dynamic color ───────────────────────────────────────────────────────────

def embed_color(codes: list[int], avg_temp: float, total_precip: float) -> int:
    """
    Calculates embed sidebar color from weather codes, average temperature,
    and total precipitation. Considers condition type and temperature together
    for a truly dynamic result.
    """
    if not codes:
        return 0x808B96

    worst       = max(codes)
    has_snow    = any(is_snow(c) for c in codes)
    has_rain    = any(is_rain(c) for c in codes)
    heavy_rain  = any(c in (63, 65, 81, 82) for c in codes)
    n           = len(codes)
    clear_ratio = sum(1 for c in codes if c <= 1) / n
    cloud_ratio = sum(1 for c in codes if c == 3) / n
    is_clear    = clear_ratio > 0.5
    is_overcast = cloud_ratio > 0.5

    # Storms always override
    if worst >= 95:
        return 0x4A235A  # deep violet

    # Snow: colour based on temperature
    if has_snow:
        if avg_temp < -5:
            return 0xD6EAF8  # near-white arctic
        if avg_temp < 2:
            return 0xAED6F1  # ice blue
        return 0x85C1E9      # slushy — slightly warmer blue

    # Rain: intensity + temp shift the blue
    if heavy_rain or total_precip > 8:
        return 0x1B4F72 if avg_temp < 8 else 0x2874A6   # dark navy / ocean blue
    if has_rain and total_precip > 3:
        return 0x2E86C1      # solid rain blue
    if has_rain:
        return 0x5DADE2      # light sky blue

    # Fog
    if worst in (45, 48):
        return 0x99A3A4      # muted grey

    # Temperature-driven colours, modified by cloud cover
    if avg_temp >= 28:
        return 0xC0392B      # scorching red

    if avg_temp >= 23:
        return 0xE67E22 if is_overcast else 0xF39C12   # orange-amber

    if avg_temp >= 18:
        if is_clear:    return 0xF4D03F   # sunny gold
        if is_overcast: return 0xF0B27A   # warm amber
        return 0xF8C471                   # soft amber

    if avg_temp >= 12:
        if is_clear:    return 0x27AE60   # spring green
        if is_overcast: return 0x7DCEA0   # sage green
        return 0x52BE80                   # mid green

    if avg_temp >= 5:
        if is_clear:    return 0x5499C7   # cool blue
        if is_overcast: return 0x717D7E   # grey
        return 0x76A5C9                   # dusty blue

    if avg_temp >= 0:
        if is_clear:    return 0x7FB3D3   # icy clear blue
        return 0x566573                   # cold grey

    if avg_temp >= -10:
        return 0x85C1E9   # freezing — ice blue
    return 0xD6EAF8       # arctic — pale white-blue

# ─── Clothing tip ────────────────────────────────────────────────────────────

def clothing_tip(rows: list[dict]) -> str:
    avg_temp   = sum(r["temp"]   for r in rows) / len(rows)
    min_temp   = min(r["temp"]   for r in rows)
    max_temp   = max(r["temp"]   for r in rows)
    total_prec = sum(r["precip"] for r in rows)
    max_wind   = max(r["wind"]   for r in rows)
    codes      = [r["code"]      for r in rows]

    has_storm  = any(c >= 95            for c in codes)
    heavy_rain = any(c in (63, 65, 81, 82) for c in codes)
    has_rain   = total_prec > 0.5
    has_snow   = any(is_snow(c)         for c in codes)
    very_windy = max_wind > 15
    windy      = max_wind > 8

    tips = []

    # Precipitation & snow first
    if has_storm:
        tips.append("⛈️ Stay indoors if you can — storm incoming")
    elif heavy_rain:
        tips.append("🌧️ Raincoat & waterproof boots")
    elif has_rain:
        tips.append("☂️ Bring an umbrella")

    if has_snow:
        tips.append("❄️ Heavy winter gear" if avg_temp < -5 else "❄️ Warm boots & coat")

    # Wind
    if very_windy:
        tips.append("💨 Very windy — windproof layer a must")
    elif windy and avg_temp < 10:
        tips.append("💨 Wind chill is real — feels colder than it looks")

    # Temperature (only when no rain/snow/storm driving the outfit)
    if not has_rain and not has_snow and not has_storm:
        if max_temp >= 28:
            tips.append("🌡️ Scorcher — stay hydrated & sunscreen up")
        elif max_temp >= 23:
            tips.append("😎 Shorts & t-shirt weather")
        elif max_temp >= 18:
            tips.append("👕 T-shirt midday, light layer for later" if min_temp < 12 else "👕 T-shirt weather")
        elif avg_temp >= 12:
            tips.append("🧥 Light jacket recommended")
        elif avg_temp >= 5:
            tips.append("🧥 Bring a jacket")
        elif avg_temp >= 0:
            tips.append("🧥 Coat weather — bundle up")
        else:
            tips.append("🧤 Full winter gear — gloves, hat, the works")

    return "  ·  ".join(tips) if tips else "👍 Looks decent out there"

# ─── Data fetching ───────────────────────────────────────────────────────────

def fetch_open_meteo(forecast_days: int = 2) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=temperature_2m,apparent_temperature,precipitation"
        ",windspeed_10m,winddirection_10m,weathercode"
        "&wind_speed_unit=ms"
        "&timezone=Europe%2FStockholm"
        f"&forecast_days={forecast_days}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()["hourly"]

def fetch_smhi(target_dates: set) -> dict:
    """Returns {date: {hour: {t, pmean}}} for each date in target_dates."""
    url = (
        f"https://opendata-download-metfcst.smhi.se/api/category/pmp3g/version/2"
        f"/geotype/point/lon/{LON}/lat/{LAT}/data.json"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    result: dict = {}
    for ts in resp.json()["timeSeries"]:
        dt = datetime.fromisoformat(ts["validTime"].replace("Z", "+00:00")).astimezone(TZ)
        d  = dt.date()
        if d not in target_dates:
            continue
        params = {p["name"]: p["values"][0] for p in ts["parameters"]}
        result.setdefault(d, {})[dt.hour] = {
            "t":     params.get("t"),
            "pmean": params.get("pmean") or 0,
        }
    return result

# ─── Daily digest ────────────────────────────────────────────────────────────

def build_daily_rows() -> list[dict]:
    om  = fetch_open_meteo(forecast_days=2)
    now = datetime.now(TZ)
    today    = now.date()
    tomorrow = (now + timedelta(days=1)).date()

    smhi = {}
    try:
        smhi = fetch_smhi({today, tomorrow})
    except Exception as e:
        log.warning(f"SMHI unavailable ({e}), using Open-Meteo only.")

    rows = []
    for (hour, day_offset) in FORECAST_SLOTS:
        idx      = day_offset * 24 + hour
        day_date = today if day_offset == 0 else tomorrow

        om_t    = om["temperature_2m"][idx]
        om_ft   = om["apparent_temperature"][idx]
        om_prec = om["precipitation"][idx]
        om_ws   = om["windspeed_10m"][idx]
        om_wd   = om["winddirection_10m"][idx]
        om_code = om["weathercode"][idx]

        smhi_row  = smhi.get(day_date, {}).get(hour, {})
        smhi_t    = smhi_row.get("t")
        smhi_prec = smhi_row.get("pmean") or 0

        temp   = round((om_t + smhi_t) / 2, 1) if smhi_t is not None else round(om_t, 1)
        precip = round(max(om_prec, smhi_prec), 1)
        emoji, desc = WMO.get(om_code, ("🌡️", "Unknown"))

        rows.append({
            "hour":     hour,
            "offset":   day_offset,
            "temp":     temp,
            "feels":    round(om_ft, 1),
            "precip":   precip,
            "wind":     round(om_ws, 1),
            "wind_dir": wind_dir(om_wd),
            "code":     om_code,
            "emoji":    emoji,
            "desc":     desc,
        })
    return rows

def build_daily_embed(rows: list[dict]) -> dict:
    now     = datetime.now(TZ)
    day_str = now.strftime("%A, %d %B")
    current = rows[0]

    all_codes    = [r["code"]   for r in rows]
    avg_temp     = sum(r["temp"] for r in rows) / len(rows)
    total_precip = sum(r["precip"] for r in rows)

    color = embed_color(all_codes, avg_temp, total_precip)
    tip   = clothing_tip(rows)

    lines      = []
    prev_off   = -1
    for r in rows:
        if r["offset"] != prev_off:
            if r["offset"] == 1:
                lines.append("")   # blank separator before midnight slots
            prev_off = r["offset"]
        rain_s = f"  💧 {r['precip']}mm" if r["precip"] >= 0.1 else ""
        lines.append(
            f"{r['emoji']}  `{r['hour']:02d}:00`  **{fmt_temp(r['temp'])}**  {r['desc']}{rain_s}"
        )

    description = (
        f"**{current['emoji']} {fmt_temp(current['temp'])}C** — {current['desc']}\n"
        f"Feels like **{fmt_temp(current['feels'])}C**  ·  "
        f"💨 **{current['wind']} m/s** {current['wind_dir']}\n\n"
        f"*{tip}*\n\n"
        f"**Forecast**\n"
        f"{'━' * 26}\n"
        + "\n".join(lines)
    )

    return {
        "title":       f"🌍  Jönköping  ·  {day_str}",
        "description": description,
        "color":       color,
        "footer":      {"text": "Open-Meteo · SMHI  |  Jönköping, Sweden"},
        "timestamp":   now.isoformat(),
    }

# ─── Weekly overview ─────────────────────────────────────────────────────────

def build_weekly_rows() -> list[dict]:
    now = datetime.now(TZ)
    # Always start from Monday — offset 0 if today is Monday, else next Monday
    days_to_monday = (7 - now.weekday()) % 7
    om = fetch_open_meteo(forecast_days=days_to_monday + 7)

    days = []
    for i in range(7):
        day_offset = days_to_monday + i
        start = day_offset * 24
        end   = start + 24

        temps   = om["temperature_2m"][start:end]
        precips = om["precipitation"][start:end]
        codes   = om["weathercode"][start:end]
        winds   = om["windspeed_10m"][start:end]

        dom     = dominant_code(codes)
        emoji, desc = WMO.get(dom, ("🌡️", "Unknown"))

        days.append({
            "date":     (now + timedelta(days=day_offset)).date(),
            "low":      round(min(temps), 1),
            "high":     round(max(temps), 1),
            "avg":      round(sum(temps)   / len(temps),   1),
            "precip":   round(sum(precips), 1),
            "wind_max": round(max(winds),   1),
            "all_codes": codes,
            "code":     dom,
            "emoji":    emoji,
            "desc":     desc,
        })
    return days

def build_weekly_embed(days: list[dict]) -> dict:
    now      = datetime.now(TZ)
    week_num = now.isocalendar()[1]

    all_codes    = [c for d in days for c in d["all_codes"]]
    week_avg     = sum(d["avg"]    for d in days) / len(days)
    week_precip  = sum(d["precip"] for d in days)

    color = embed_color(all_codes, week_avg, week_precip)

    lines = []
    for d in days:
        date_s = d["date"].strftime("%a %d")
        rain_s = f"  💧 {d['precip']}mm" if d["precip"] >= 0.5 else ""
        lines.append(
            f"{d['emoji']}  **{date_s}**  "
            f"{fmt_temp(d['low'])} → {fmt_temp(d['high'])}  "
            f"{d['desc']}{rain_s}"
        )

    best    = min(days, key=lambda d: (d["code"], -d["high"]))
    wettest = max(days, key=lambda d: d["precip"])

    summary = [f"Best day: {best['emoji']} **{best['date'].strftime('%A')}**"]
    if wettest["precip"] >= 1.0:
        summary.append(
            f"Wettest: 🌧️ **{wettest['date'].strftime('%A')}** ({wettest['precip']}mm)"
        )

    description = (
        f"**7-day forecast**\n"
        f"{'━' * 26}\n"
        + "\n".join(lines)
        + f"\n\n*{'  ·  '.join(summary)}*"
    )

    return {
        "title":       f"📅  Jönköping  ·  Week {week_num}",
        "description": description,
        "color":       color,
        "footer":      {"text": "Open-Meteo  |  Jönköping, Sweden"},
        "timestamp":   now.isoformat(),
    }

# ─── Send functions ──────────────────────────────────────────────────────────

def send_daily():
    log.info("Sending daily weather digest...")
    try:
        rows  = build_daily_rows()
        embed = build_daily_embed(rows)
        resp  = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        log.info("Daily digest sent.")
    except Exception as e:
        log.error(f"Daily digest failed: {e}", exc_info=True)

def send_weekly():
    log.info("Sending weekly weather overview...")
    try:
        days  = build_weekly_rows()
        embed = build_weekly_embed(days)
        resp  = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        log.info("Weekly overview sent.")
    except Exception as e:
        log.error(f"Weekly overview failed: {e}", exc_info=True)

# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    log.info("Weather bot started.")
    log.info("Daily digest:   08:00 every day")
    log.info("Weekly outlook: 08:00 every Monday (in addition to daily)")

    if os.getenv("SEND_ON_START", "false").lower() == "true":
        log.info("SEND_ON_START=true — sending both digests now.")
        send_daily()
        send_weekly()

    schedule.every().day.at("08:00").do(send_daily)
    schedule.every().monday.at("08:00").do(send_weekly)

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    main()

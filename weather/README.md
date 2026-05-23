# Weather Digest

Discord bot that posts a daily forecast at 08:00 and a 7-day overview at 08:00 every Monday. Blends Open-Meteo (multi-model — ECMWF + GFS) with SMHI's open-data point forecast for the same coordinates and averages temperature when both sources agree.

Coordinates are hardcoded to Jönköping (57.7826 N, 14.1618 E) since this runs on a single home server; pull them out to env if you fork it.

## What it does

- Daily digest hits 10 hourly slots (08:00 → 22:00 + 00:00 / 02:00 next day, the last two so a Friday night out gets a forecast for the walk home).
- Weekly overview picks a "best day" (lowest weather code, highest high) and a "wettest day" if precipitation crosses 1mm.
- Embed sidebar colour is computed from the day's weather codes, average temp, and total precipitation — same data → same colour, but a sunny 25°C looks different from an overcast 5°C.
- Clothing tip is rule-based: storm/heavy-rain wins, then snow, then wind, then temperature.

## Stack

- `requests` for both providers
- `schedule` for the daily/weekly trigger (deliberately not APScheduler — this needs ~50 lines, not a framework)
- `pytz` for Europe/Stockholm DST handling
- Containerized via Dockerfile + compose

## Configuration

```
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
TZ=Europe/Stockholm
SEND_ON_START=false   # set true on first deploy to verify formatting
```

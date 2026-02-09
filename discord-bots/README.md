# Discord Bots

## clan-war-bot

Full-featured Clash of Clans clan management bot. Tracks wars, CWL seasons, attack performance, and sends automated reminders.

### Features

- Real-time war tracking with attack/defense monitoring
- CWL (Clan War Leagues) season automation and scoring
- Attack reminders for members who haven't used their attacks
- Performance analytics and historical data
- Dockerized deployment with SQLite persistence

### Setup

```bash
cp .env.example .env  # Fill in API_KEY, BOT_TOKEN, GUILD_ID
docker compose up -d
```

## phd-monitor

Monitors Cybercampus Sweden PhD listings for when application links become active at specific universities. Sends instant Discord notifications when a position opens.

### How It Works

1. Polls the Cybercampus graduate page every 15 minutes
2. Parses the HTML for university listings
3. Detects state transitions (no link -> active link -> filled)
4. Sends a Discord alert on any state change
5. Persists state to avoid duplicate notifications

### Setup

```bash
docker compose up -d
```

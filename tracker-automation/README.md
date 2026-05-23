# Tracker Automation

Management system for two private BitTorrent trackers (TorrentLeech, DigitalCore). Handles seeding health, stalled-torrent cleanup, Hit-and-Run resolution, daily reporting, and a fuzzy/ML name matcher to bridge tracker listings and the local qBittorrent client.

## Layout

```
core/                   Tracker monitors and reporters
  torrentleech.py       TL monitor (cookie + Cloudflare bypass)
  digitalcore.py        DC monitor
  tl_reporter.py        TL daily stats reporter
  dc_reporter.py        DC daily stats reporter
  tl_big_boys.py        Long-term seeding leaderboard scrape
  base_monitor.py       Shared monitor base class
  achievements.py       Achievement / milestone tracking

lib/
  auth/                 Cookie + chromedriver session helpers, Cloudflare bypass
  matching/             Name matching pipeline
    normalizer.py       Token extraction, codec/group/tag stripping
    matcher.py          Fuzzy matcher (lexical + size validation)
    features.py         Feature extraction for the ML model
    ml_model.py         Trained scikit-learn model wrapper
    ml_matcher.py       Hybrid lexical/ML matcher
    translation_matcher.py  Non-English title fallback
    training.py         Collect labelled (tracker, qbit) pairs at runtime
    feedback.py         Human-in-the-loop feedback storage
    recovery_queue.py   Re-attempt queue for previously unmatched torrents
    retry_queue.py      Backoff retry for transient failures
  notifications/        Discord webhook formatter
  qbit/                 qBittorrent API wrapper
  bandwidth_manager.py  Dynamic upload limit based on active Jellyfin streams
  torrent_lifecycle.py  HnR + lifecycle event tracking
  tracker_utils.py      Shared utilities

qbittorrent/            Maintenance services (run as Docker containers)
  seeder.py             Force-start stalled uploads
  announcer.py          Continuous reannounce loop
  cleaner.py            Orphan detection
  inspector.py          State inspection + diagnostics
  queue.py              Queue management for large (1 TiB+) torrents

tools/
  add_verified_matches.py   Manually label tricky pairs into the training set
  train_model.py            Train and serialize the matching model

tl-visitor/             Standalone container that keeps a TL session warm
                        via a real Chromium profile + Xvfb. Avoids Selenium
                        for the daily "logged-in" visit that maintains the
                        consecutive-days counter.

run/                    Cron-friendly host runner scripts
docker/                 Containers for the qBittorrent maintenance services
```

## Operation

Each tracker has a 2-hour monitor and a daily reporter. The monitor:

1. Logs in via stored cookies; on TL, undetected-chromedriver clears Cloudflare.
2. Resolves any Hit-and-Run warnings by re-downloading the affected torrents into a watch folder.
3. Scrapes every page of the seeding profile.
4. Updates rolling per-torrent upload-rate snapshots.
5. Flags torrents whose upload rate has fallen below the per-tracker threshold for too long.
6. Resolves each flagged tracker name to a qBittorrent torrent using the matching pipeline.
7. Removes confirmed stalls, records each match/miss as training data.
8. Posts a Discord summary.

## Name matching

Tracker and client names usually drift apart (dots vs spaces, codec tags, scene group, release year, language tags). The pipeline normalizes both sides, scores them with a combination of token overlap, fuzz ratios, and size sanity checks, and falls back to a trained model when lexical scoring is ambiguous. Every decision is logged as `(tracker_name, qbit_name, label)` so the training set grows from real traffic.

`translation_matcher` handles non-Latin titles by matching against romanized variants and known foreign-language alternates.

## Configuration

All secrets and host-specific values come from a `.env` file at the repo root. See `.env.example` (not committed) for the full list:

- TorrentLeech: `TL_USERNAME`, `TL_PASSWORD`
- DigitalCore: `DC_USER_ID`, `DC_USER_HANDLE`, cookies in `storage/json/`
- qBittorrent: `QBIT_URL`, `QBIT_USER`, `QBIT_PASS`
- Discord: `DISCORD_TORRENT_HOOK`, `DISCORD_STATS_HOOK`
- Bandwidth manager: `LAN_SUBNET`, `JELLYFIN_API`

## Stack

Python 3.11, Selenium + undetected-chromedriver, requests, BeautifulSoup, rapidfuzz, scikit-learn, qbittorrent-api, Docker.

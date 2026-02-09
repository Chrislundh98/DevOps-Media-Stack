# Tracker Automation

Automated management system for private BitTorrent trackers. Handles seeding health monitoring, stalled torrent cleanup, Hit-and-Run (HnR) resolution, and daily statistics reporting.

## Architecture

```
core/                   # Tracker-specific monitors and reporters
  torrentleech.py       # TorrentLeech monitor (Cloudflare-protected)
  digitalcore.py        # DigitalCore monitor
  tl_reporter.py        # TorrentLeech daily stats reporter
  dc_reporter.py        # DigitalCore daily stats reporter
  base_monitor.py       # Shared base class for monitors
  achievements.py       # Achievement tracking (long-term seeding milestones)

lib/                    # Shared libraries
  auth/                 # Authentication modules
    cloudflare.py       # Cloudflare challenge bypass
    cookies.py          # Cookie-based session management
    tl_client.py        # TorrentLeech HTTP client
  matching/             # Torrent name matching engine
    matcher.py          # Fuzzy matching between tracker and qBittorrent names
    normalizer.py       # Name normalization (strip tags, codecs, groups)
    training.py         # Training data collection for ML pipeline
  notifications/        # Discord notification formatting
  qbit/                 # qBittorrent API client wrapper
  bandwidth_manager.py  # Dynamic bandwidth throttling based on Jellyfin streams
  tracker_utils.py      # Shared utility functions

qbittorrent/            # qBittorrent management utilities
  announcer.py          # Continuous tracker reannounce loop
  seeder.py             # Force-start seeding for stalled uploads
  queue.py              # Large torrent queue management (1 TiB+)
  cleaner.py            # Orphaned torrent detection and cleanup
  inspector.py          # Torrent state inspection and diagnostics

docker/                 # Container configuration (qBittorrent utilities only)
scripts/                # Host-side cron runner scripts
```

## How It Works

### Monitor Cycle (runs every 2 hours)

1. Authenticate via saved cookies (TL uses undetected-chromedriver for Cloudflare)
2. Check and resolve any Hit-and-Run warnings by downloading affected torrents
3. Scrape seeding page across all pages
4. Track upload history per torrent with rolling snapshots
5. Flag stalled torrents (low upload relative to time and size)
6. Match flagged names to qBittorrent entries using fuzzy matching
7. Remove confirmed stalled torrents and log training data
8. Send Discord notification with results

### Name Matching

Tracker names and qBittorrent names often differ (dots vs spaces, missing tags, different formatting). The matching engine normalizes both names, compares them using multiple strategies (exact, fuzzy, containment), and validates matches by file size. Every match/miss is logged as training data for a planned ML upgrade.

### Daily Reporter

Scrapes profile statistics (ratio, upload, download, bonus points), calculates match accuracy trends over 7/30/all-time windows, tracks top seeders and storage savings, and sends a formatted Discord embed.

## Configuration

All sensitive values are loaded from environment variables via `.env`:

```env
USERNAME=your_tracker_username
PASSWORD=your_tracker_password
QBIT_URL=http://192.168.x.x:8080
QBIT_USER=admin
QBIT_PASS=your_password
DISCORD_TORRENT_HOOK=https://discord.com/api/webhooks/...
DISCORD_STATS_HOOK=https://discord.com/api/webhooks/...
```

## Deployment

Browser-based scripts run on the host (require Chrome 141 + undetected-chromedriver for Cloudflare bypass). qBittorrent utilities run in Docker containers.

```bash
# Start Docker services
cd docker && docker compose up -d

# Host cron (example)
0 */2 * * * /path/to/scripts/run_tl_monitor.sh
30 */2 * * * /path/to/scripts/run_dc_monitor.sh
55 7 * * * /path/to/scripts/run_tl_reporter.sh
0 8 * * * /path/to/scripts/run_dc_reporter.sh
```

# Christoffer Lundh

Cybersecurity, system administration, automation, and full-stack Python.

Jönköping University, Sweden — MSc Cybersecurity (ongoing), BSc Computer Engineering & Informatics.

---

## About

This repository is the code behind a single-host Debian server that I designed, built, and operate. Everything here runs in production — daily, on real hardware, with real data — and has been iterated on over months. It covers the full stack of a small ops org: container orchestration, VPN/routing, monitoring + alerting, scheduled batch jobs, Discord-driven automation, and a few one-off tools that solve specific problems on the same box.

Background spans networking (Cisco CCNA, Telia NOC), system administration (Linux, Docker, NAS architecture), and cybersecurity (published researcher, CTF competitor, co-founder of pwnJU).

## Publications

### [Evaluating Security and Data Privacy in Smart Home Devices](https://ceur-ws.org/Vol-4134/paper27.pdf)
**Conference:** 11th International Workshop on Socio-Technical Perspectives in Information Systems (STPIS 2025)
*Natalia Khetagourova, Christoffer Lundh, Joakim Kävrestad*

Evaluated the data-privacy and security practices of self-declared CE-compliant smart-home IoT devices under EU RED 2014/53/EU. Captured and analyzed each device's network traffic, compared it against the vendor's stated data-handling policy, and documented the discrepancies.

---

## Projects

### [tracker-automation/](./tracker-automation/)
Multi-tracker management system for two private BitTorrent communities. Monitors seeding health on 2-hour cycles, automatically resolves Hit-and-Run warnings, runs a fuzzy + ML name matcher that bridges tracker listings and the local qBittorrent client, and ships a daily Discord-embed report with rolling 7/30-day accuracy trends. Cloudflare bypass via undetected-chromedriver. A separate "tl-visitor" container keeps a real Chromium profile warm for the daily login-streak visit.
**Stack:** Python 3.11, Selenium, undetected-chromedriver, qbittorrent-api, scikit-learn, rapidfuzz, BeautifulSoup, Docker.

### [docker-stacks/](./docker-stacks/)
Two production `docker-compose` stacks (`core/`, `media/`) sharing an external network. Core: nginx-proxy-manager, two isolated WireGuard tunnels, Glances, Wizarr, Jellystat + Postgres, Watchtower. Media: full *arr suite routed through a single gluetun VPN tunnel (kill-switch by design), qBittorrent, Tdarr/MakeMKV/mkvtoolnix, Jellyfin/Jellyseerr. Per-container `cpuset` / `cpu_shares` / `mem_limit` tuning so user-facing services win scheduler contention against background jobs.
**Stack:** Docker Compose, NGINX, WireGuard, gluetun, Postgres, Watchtower.

### [monitor/](./monitor/)
Self-hosted telemetry + alerting daemon. APScheduler loop collects CPU/RAM/IO/temperatures/SMART data on a 30s tick into SQLite. Two-tier alerting: hard hardware safety limits fire immediately (with cooldown), everything else uses an adaptive `mean + 3σ` threshold built from rolling hourly baselines, with absolute floors to suppress statistically-unusual-but-fine values. Daily PNG digest sent to Discord.
**Stack:** Python 3.12, APScheduler, psutil, smartctl, matplotlib, SQLite, Docker.

### [weather/](./weather/)
Discord bot that posts a daily forecast at 08:00 and a 7-day overview every Monday. Blends Open-Meteo (multi-model — ECMWF + GFS) and SMHI for the same coordinates, computes embed sidebar colour and a clothing tip from weather code + average temperature + total precipitation.
**Stack:** Python 3.11, requests, schedule, Docker.

### [discord-bots/clan-war-bot/](./discord-bots/clan-war-bot/)
Clash of Clans clan-management bot. Polls the Supercell API for the current war + CWL round, schedules attack reminders, tracks store offers. `/chat-ai` routes through a sidecar Ollama (llama3.2:3b on CPU pinned to P-cores) with a sandboxed persona — prompt-injection refusal, strict CONTEXT-only stat grounding, post-processor that strips stage directions and stray pings. Restart policy intentionally `on-failure:5` so a Discord 429 storm can't snowball into a token-identity lockout.
**Stack:** discord.py, Ollama, SQLite, asyncio, Docker.

### [discord-bots/phd-monitor/](./discord-bots/phd-monitor/)
Polls a graduate-position listings page every 15 minutes, detects state transitions (`no link → active → filled`) per university, and posts a Discord alert when something opens. Built because positions at certain Swedish universities open with no notice and fill within hours.
**Stack:** Python 3.11, BeautifulSoup, requests, Docker.

### [infrastructure/](./infrastructure/)
Shell scripts that keep the host alive: daily rsync + tar backup with rotation; a SnapRAID wrapper with preflight (mount checks, SMART, active-writer detection) and Discord notifications; a script that fixes the default audio track on MKV files via a Docker-hosted mkvtoolnix; ionice for the qBittorrent container on boot; an ownership reset; and a post-reboot sanity check.
**Stack:** Bash, rsync, SnapRAID, smartmontools, Docker.

### [media-tools/](./media-tools/)
Small scripts that interop with the media stack: ISO ripping via MakeMKV (Docker), RAR auto-extraction for downloaded archives, audio-track inspection, and a Bazarr Extended-Edition subtitle fixer.
**Stack:** Python 3.11, Docker exec, Bazarr API.

### [whisper/](./whisper/)
Structured interview transcriber. faster-whisper (large-v3) with automatic CUDA / int8-CPU fallback, VAD pre-filtering, pause-based speaker detection, optional interview-guide question mapping, DOCX / TXT / JSON output. Built for one-on-one research interviews.
**Stack:** Python 3.10+, faster-whisper, CTranslate2, python-docx.

### [documentation/](./documentation/)
A working set of personal references — Bash, Docker, Git, Python, networking, security — written as I encounter problems on the box. Not a tutorial repo, just notes I keep coming back to.

---

## Skills

| Area | Tools |
| --- | --- |
| Languages | Python, Bash, SQL, basic JavaScript |
| Containers | Docker, Docker Compose, Watchtower, gluetun |
| Networking | Cisco (CCNA), WireGuard, NGINX Proxy Manager, mergerfs |
| Linux | Debian, systemd, SnapRAID, rsync, smartmontools, ionice, cron |
| Cybersecurity | Penetration testing, ISO 27001, cryptography, CTF, IoT privacy research |
| Automation | Selenium / undetected-chromedriver, web scraping, REST API integration, scheduled jobs (APScheduler / cron) |
| Data | SQLite, Postgres, rapidfuzz, scikit-learn for lexical/ML matching |
| Monitoring | Custom telemetry stack, Discord embeds, adaptive baselines, SMART |

---

## Host topology

```
Debian Server (i5-13500, 64 GB DDR5, NVMe boot + 3x WD Red Pro behind SnapRAID + mergerfs)
|
|-- docker / compose
|    |-- core stack      nginx-proxy-manager, 2 x WireGuard, Glances, Wizarr, Jellystat + Postgres, Watchtower
|    |-- media stack     gluetun, Radarr, Sonarr, Prowlarr, Bazarr, autobrr, qBittorrent, flaresolverr, unpackerr,
|    |                   Tdarr, MakeMKV, mkvtoolnix, Jellyfin, Jellyseerr
|    \-- standalone      system-monitor, weather-bot, clan-war-bot + Ollama, phd-monitor, tracker maintenance containers
|
|-- host services (cron + systemd)
|    |-- tracker monitors / reporters (2 trackers, 2-hour cycle + daily report)
|    |-- snapraid-sync (preflight-guarded, daily)
|    |-- backup.sh       (daily rsync + tar.gz, 7-day rotation)
|    \-- media processors (mkv audio track fixer, Bazarr Extended fixer, MakeMKV auto-rip)
|
\-- storage
     |-- 3x WD Red Pro pooled via mergerfs, parity via SnapRAID (~50 TB usable)
     \-- NVMe scratch for in-progress torrents (rsync'd to pool post-import)
```

---

## Contact

- Email: christoffer.lundh98@gmail.com
- LinkedIn: [linkedin.com/in/christoffer-lundh-639322235](https://www.linkedin.com/in/christoffer-lundh-639322235/)
- University: luch22gv@student.ju.se

Open to roles in cybersecurity, DevOps, infrastructure / Linux administration, and Python automation.

---

## License

MIT. See [LICENSE](./LICENSE).

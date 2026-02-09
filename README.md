# Christoffer Lundh

**Automation Engineer | Cybersecurity Student | Infrastructure Developer**

Jönköping University, Sweden | MSc Cybersecurity (ongoing) | BSc Computer Engineering and Informatics

---

## About

I design and maintain automated infrastructure that runs 24/7 on self-hosted hardware. This repository contains production code from systems I have built, deployed, and operate daily. Every project here solves a real problem and has been refined through months of iteration.

My background spans networking (Cisco CCNA, Telia NOC), system administration (Linux, Docker, NAS & Cloud architecture), cybersecurity (published researcher, CTF competitor, co-founder of pwnJU), and full-stack automation with Python.

## Publications

### [Evaluating Security and Data Privacy in Smart Home Devices](https://ceur-ws.org/Vol-4134/paper27.pdf)
**Conference:** 11th International Workshop on Socio-Technical Perspectives in Information Systems (STPIS 2025)
*Natalia Khetagourova, Christoffer Lundh, Joakim Kävrestad*

Critically evaluated the data privacy and security practices of smart-home IoT devices, specifically those self-declaring CE compliance under EU RED directive 2014/53/EU. Analyzed network traffic and privacy policies to identify discrepancies between stated and actual data handling practices.

---

## Projects

### [tracker-automation/](./tracker-automation/)
Private BitTorrent tracker management system. Monitors seeding health, detects stalled torrents, manages Hit-and-Run obligations, and reports daily statistics across multiple trackers. Features include Cloudflare bypass, fuzzy name matching between tracker and client with training data collection for future ML improvements, and Discord notifications.

**Technologies:** Python, Selenium, undetected-chromedriver, qBittorrent API, Docker, BeautifulSoup, Discord Webhooks

**Scale:** ~5,400 lines of Python across 20+ modules, managing 1,100+ active torrents

### [discord-bots/clan-war-bot/](./discord-bots/clan-war-bot/)
Clash of Clans clan management bot with full war tracking, CWL (Clan War Leagues) automation, attack reminders, and performance analytics. Integrates with the Supercell API and persists data in SQLite for historical reporting.

**Technologies:** discord.py, Supercell CoC API, SQLite, Docker, asyncio

**Scale:** ~3,400 lines across 7 modules

### [discord-bots/phd-monitor/](./discord-bots/phd-monitor/)
Automated monitor for PhD position listings at Cybercampus Sweden. Polls university pages on a schedule, detects when application links become active, and sends instant Discord alerts. Built to catch time-sensitive opportunities.

**Technologies:** Python, BeautifulSoup, Docker, Discord Webhooks

### [media-tools/](./media-tools/)
Media processing pipeline for automated disc ripping, audio track correction, and subtitle management. Integrates with MakeMKV, mkvtoolnix, and Bazarr for end-to-end media handling.

**Technologies:** Python, MakeMKV, mkvtoolnix, Bazarr API

### [infrastructure/](./infrastructure/)
System maintenance scripts for NAS operations: automated backups, disk health monitoring, Docker container updates, filesystem ownership management, and audio stream repair.

**Technologies:** Bash, Python, Docker API

---

## Technical Skills

| Area | Technologies |
|---|---|
| Languages | Python, Bash, SQL |
| Containers | Docker, Docker Compose |
| Networking | Cisco (CCNA), WireGuard, Gluetun VPN, Nginx Proxy Manager |
| Security | Penetration testing, ISO 27001, cryptography, CTF competitions |
| Automation | Selenium, web scraping, API integration, cron scheduling |
| Infrastructure | Linux (Debian/Ubuntu), Synology NAS, NVIDIA GPU passthrough |
| Monitoring | Discord webhooks, custom alerting, log analysis |
| Version Control | Git, GitHub |

---

## Architecture Overview

```
NAS (Synology / Debian)
|
|-- Docker Containers
|   |-- qBittorrent utilities (force-seeder, queue manager, announcer)
|   |-- Clan War Bot
|   |-- PhD Monitor
|
|-- Host Services (cron-scheduled)
|   |-- TorrentLeech monitor + reporter
|   |-- DigitalCore monitor + reporter
|   |-- Media processing pipeline
|   |-- System maintenance scripts
|
|-- Storage
    |-- /volume2/data (media library, 40TB+)
    |-- /volume1/automation (code, configs, state)
```

---

## Contact

- Email: christoffer.lundh98@gmail.com
- LinkedIn: [linkedin.com/in/christoffer-lundh-639322235](https://www.linkedin.com/in/christoffer-lundh-639322235/)
- University: luch22gv@student.ju.se

I am actively seeking roles in cybersecurity, DevOps, automation engineering, and Linux/Python development. Open to discussing positions or collaboration.

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

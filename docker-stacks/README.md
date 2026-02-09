# Docker Stacks

Production Docker Compose configurations running on a NAS. Two stacks with clear separation of concerns.

## Core Services (`core-services.yml`)

Infrastructure and monitoring services that run on the host network or the shared Docker network.

| Service | Purpose | Port |
|---|---|---|
| Nginx Proxy Manager | Reverse proxy with SSL termination | 81 (admin), 8081/8443 |
| WireGuard | VPN server for remote access (8 peers) | 51820/udp |
| WireGuard Admin | Separate VPN for administrative access (3 peers) | 51821/udp |
| Glances | Real-time system monitoring | Host network |
| Wizarr | Media server invitation management | 5690 |
| Jellystat + PostgreSQL | Jellyfin analytics and statistics | 3000 |

## Media Stack (`media-stack.yml`)

All media acquisition and processing services. Download traffic is routed through Gluetun VPN.

| Service | Purpose | Network |
|---|---|---|
| Gluetun | WireGuard VPN tunnel for all download traffic | Bridge (exposes ports) |
| Radarr | Movie management and automation | VPN (via Gluetun) |
| Sonarr | TV series management and automation | VPN (via Gluetun) |
| Prowlarr | Indexer management | VPN (via Gluetun) |
| FlareSolverr | Cloudflare challenge solver for indexers | VPN (via Gluetun) |
| qBittorrent | BitTorrent client | VPN (via Gluetun) |
| Autobrr | IRC-based torrent automation | VPN (via Gluetun) |
| Bazarr | Subtitle management | VPN (via Gluetun) |
| Tdarr | Media transcoding (GPU-accelerated) | Shared network |
| MakeMKV | Disc ripping (web UI) | Shared network |
| MKVToolNix | MKV editing (web UI) | Shared network |
| Unpackerr | Automatic archive extraction | Shared network |
| Jellyfin | Media server (Intel QuickSync HW transcoding) | Shared network |
| Jellyseerr | Media request management | Shared network |

## Architecture
```
Internet
  |
  +-- Nginx Proxy Manager (SSL termination)
  |     |
  |     +-- Jellyfin (streaming)
  |     +-- Jellyseerr (requests)
  |     +-- Wizarr (invitations)
  |
  +-- WireGuard VPN (remote access)
  |
  +-- Gluetun VPN Tunnel
        |
        +-- qBittorrent (torrents)
        +-- Radarr / Sonarr (automation)
        +-- Prowlarr + FlareSolverr (indexers)
        +-- Bazarr (subtitles)
        +-- Autobrr (IRC automation)
```

All download-related services use `network_mode: service:gluetun` to ensure traffic is routed through the VPN. If the VPN drops, these services lose network access entirely (kill switch behavior).

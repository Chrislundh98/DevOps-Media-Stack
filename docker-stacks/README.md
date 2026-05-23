# Docker Stacks

Two compose stacks that share an external `shared_network` bridge so containers in either stack can resolve each other by name.

## core/

Reverse proxy, two WireGuard tunnels (general + admin, separate subnets/keypairs), Glances, Wizarr, Jellystat + Postgres, and Watchtower. Watchtower is label-gated, so only opted-in containers update automatically; database images and the two WireGuard containers are pinned.

Pinned IPs (`172.18.0.12` for NPM, `172.18.0.15` for Jellyfin) let the reverse proxy address backends without depending on DNS resolution order during startup.

## media/

Gluetun is the VPN gateway. The full *arr suite (Radarr, Sonarr, Prowlarr, Bazarr, autobrr, flaresolverr) plus qBittorrent and unpackerr all run with `network_mode: service:gluetun`, so a tunnel failure becomes a complete network outage for downloaders — kill-switch by design.

Jellyfin, Jellyseerr, MakeMKV, Tdarr, and mkvtoolnix sit on the regular bridge, since they shouldn't egress through the VPN.

Per-container resource notes:

| Container | CPU | Memory | Why |
| --- | --- | --- | --- |
| Jellyfin | `cpu_shares: 2048` | 8G limit, 1G reservation | User-facing — wins scheduler contention against background jobs |
| qBittorrent | `cpu_shares: 256` | 6G limit | Was using 17.6 GiB unconstrained; yields to Jellyfin |
| Tdarr | `cpuset: 12-19`, `cpu_shares: 256` | 4G | Pinned to E-cores so transcoding doesn't fight Jellyfin |
| MakeMKV / mkvtoolnix | `cpuset: 12-19` | 2G each | Same reasoning as Tdarr |
| Radarr / Sonarr / Bazarr | default | 2–4G | Bursty subtitle search / metadata refresh |

## .env

Both stacks read from a sibling `.env`. See `.env.example` for the required variables. No secret is ever inlined in compose files.

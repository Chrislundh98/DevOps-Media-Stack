# TorrentLeech Daily Visitor

Visits torrentleech.org every day at 05:15 using a real Chromium browser with a
persistent profile, keeping the consecutive-days-visited counter alive on a headless server.

## How it works

- Xvfb provides a fake display (no monitor needed)
- Chromium opens with `--user-data-dir=/profile` pointing at a persistent volume
- The profile directory stores your real logged-in session — no cookie injection, no Selenium
- Runs at 05:15 daily via an internal sleep loop

---

## Host setup (one-time, on the Debian server)

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# Log out and back in for the group change to take effect
```

---

## First-time login (create the session)

1. In `.env`, set `LOGIN_MODE=true`
2. Start the container:
   ```bash
   docker compose up -d
   ```
3. Connect with any VNC client (e.g. RealVNC, TigerVNC, Remmina) to:
   ```
   <server-ip>:5900   (no password)
   ```
4. Log into TorrentLeech in the browser window
5. Once logged in, stop the container:
   ```bash
   docker compose stop
   ```
6. Set `LOGIN_MODE=false` in `.env`

The session is now saved in `./profile/` on the host.

---

## Normal operation

```bash
docker compose up -d
```

The container runs 24/7, visits TorrentLeech at 05:15 every day, and restarts automatically on server reboot.

---

## Testing

To trigger a visit immediately without waiting for 05:15, set `RUN_ON_BOOT=true` in `.env`, then:

```bash
docker compose up   # (foreground, so you can watch the logs)
```

Set it back to `false` when done, then `docker compose up -d` for normal operation.

---

## Logs

```bash
docker logs tl-visit
docker logs -f tl-visit   # follow live
```

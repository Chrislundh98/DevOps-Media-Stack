# SSH & Remote Access

Connecting to your NAS, servers, and remote machines.

---

## Connecting

```bash
ssh user@192.168.0.80                  # Basic connection
ssh user@nas -p 2222                   # Custom port
ssh root@192.168.0.80                  # Connect as root
```

---

## SSH Keys (passwordless login)

**Step 1: Generate a key pair (on your local machine):**
```bash
ssh-keygen -t ed25519 -C "you@hostname"
# Press Enter for default location (~/.ssh/id_ed25519)
# Optionally set a passphrase
```

**Step 2: Copy public key to the server:**
```bash
ssh-copy-id user@192.168.0.80
# Or manually:
cat ~/.ssh/id_ed25519.pub | ssh user@server "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

**Step 3: Connect without password:**
```bash
ssh user@192.168.0.80    # No password prompt!
```

---

## SSH Config File

Save connection shortcuts in `~/.ssh/config`:

```
Host nas
    HostName 192.168.0.80
    User youruser
    Port 22

Host vps
    HostName 203.0.113.50
    User root
    Port 2222
    IdentityFile ~/.ssh/id_ed25519_vps
```

Now just type:
```bash
ssh nas        # Instead of ssh youruser@192.168.0.80
ssh vps        # Instead of ssh root@203.0.113.50 -p 2222
```

---

## SCP — Secure Copy

```bash
# Copy file TO remote
scp file.txt user@server:/path/

# Copy file FROM remote
scp user@server:/path/file.txt ./

# Copy folder recursively
scp -r folder/ user@server:/path/

# Custom port
scp -P 2222 file.txt user@server:/path/
```

**Tip:** For large transfers or many files, rsync over SSH is better than scp — it can resume and skips unchanged files.

---

## Run Commands Remotely

```bash
# Run a single command
ssh user@server "docker ps"

# Run multiple commands
ssh user@server "cd /volume1/automation && git pull && docker-compose restart"

# Run a script remotely
ssh user@server 'bash -s' < local_script.sh
```

---

## SSH Tunnels (Port Forwarding)

Access a service on a remote machine through your local machine.

**Local forward** — access remote service locally:
```bash
# Access remote Radarr (port 7878) at localhost:7878
ssh -L 7878:localhost:7878 user@nas

# Access remote qBittorrent (port 8080) at localhost:8080
ssh -L 8080:localhost:8080 user@nas
```

**Run tunnel in background:**
```bash
ssh -fNL 7878:localhost:7878 user@nas
# -f = background, -N = no remote command
```

---

## Key Security Tips

- Use **ed25519 keys** (fastest and most secure)
- **Disable password auth** on servers exposed to the internet (use keys only)
- **Change default SSH port** from 22 to something else on public-facing servers
- **Never use root login** over SSH on public servers — use a regular user + sudo
- Keep `~/.ssh/` permissions strict: `chmod 700 ~/.ssh && chmod 600 ~/.ssh/*`

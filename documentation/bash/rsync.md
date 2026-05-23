# Rsync Guide

rsync is the best tool for copying files — it's fast, smart (only copies what changed), and works both locally and over SSH.

---

## The Basics

```bash
rsync -av source/ destination/
```

| Flag | Meaning |
|------|---------|
| `-a` | Archive mode — preserves permissions, timestamps, symlinks, everything |
| `-v` | Verbose — shows what's being copied |
| `-n` | Dry run — shows what WOULD happen without copying anything |
| `-z` | Compress during transfer (good for remote/slow connections) |
| `-P` | Show progress + allow resume of interrupted transfers |
| `--delete` | Delete files in destination that don't exist in source (true mirror) |

**Important:** The trailing `/` on source matters!
- `rsync -av folder/ dest/` → copies the **contents** of folder into dest
- `rsync -av folder dest/` → copies the folder **itself** into dest (creates `dest/folder/`)

---

## Common Use Cases

### Copy a folder (basic backup)
```bash
rsync -av /volume1/automation/ /volume2/backup/automation/
```

### Dry run first (always a good idea for deletes)
```bash
rsync -avn --delete /volume1/automation/ /volume2/backup/automation/
```

### Sync with progress bar
```bash
rsync -avP /volume1/automation/ /volume2/backup/automation/
```

### Mirror (exact copy, removes extras in destination)
```bash
rsync -av --delete /volume1/automation/ /volume2/backup/automation/
```

---

## Excluding Files

### Exclude specific patterns
```bash
rsync -av --exclude '*.log' --exclude '__pycache__/' --exclude 'venv/' source/ dest/
```

### Exclude from a file
```bash
rsync -av --exclude-from='exclude-list.txt' source/ dest/
```

Example `exclude-list.txt`:
```
*.log
*.pyc
__pycache__/
venv/
.env
node_modules/
chrome_profile_*
*.db
nohup.out
logs/
```

---

## Remote Transfers (over SSH)

### Copy to remote server
```bash
rsync -avz /local/folder/ user@server:/remote/folder/
```

### Copy from remote server
```bash
rsync -avz user@server:/remote/folder/ /local/folder/
```

### Specify SSH port
```bash
rsync -avz -e "ssh -p 2222" /local/ user@server:/remote/
```

---

## Sending Zip Files for Dev Projects

This is the command for packaging a project folder into a zip and sending it to Claude or any dev workflow. Uses rsync to quickly get the zip to the right place.

**Step 1: Create the zip (excluding junk):**
```bash
cd /volume1/automation
zip -r /tmp/project.zip target_folder/ \
  -x "*.log" \
  -x "__pycache__/*" \
  -x "venv/*" \
  -x ".env" \
  -x "*.pyc" \
  -x "storage/training/*" \
  -x "chrome_profile_*" \
  -x "*.db"
```

**Step 2: Quick rsync to wherever you need it:**
```bash
rsync -avP /tmp/project.zip /volume2/dev-transfer/
```

**One-liner for the full workflow:**
```bash
cd /volume1 && zip -r /tmp/automation.zip automation/ \
  -x "*.log" -x "__pycache__/*" -x "venv/*" -x ".env" -x "*.pyc" \
  -x "*/storage/training/*" -x "*/chrome_profile_*" -x "*.db" \
  && echo "Ready: /tmp/automation.zip ($(du -sh /tmp/automation.zip | cut -f1))"
```

---

## Portfolio Sync (Prod → Repo)

Sync production code to your portfolio repo, excluding secrets and data:

```bash
rsync -av \
  --exclude '.env' \
  --exclude '*.db' \
  --exclude 'storage/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'state/' \
  --exclude 'nohup.out' \
  --exclude '__pycache__/' \
  --exclude 'venv/' \
  --exclude 'chrome_profile_*' \
  --exclude '*.tar.gz' \
  --exclude 'bb' \
  /volume1/automation/ /volume1/portfolio/
```

**Tip:** Save this as a script called `sync-to-portfolio.sh` so you can just run it:
```bash
#!/bin/bash
# sync-to-portfolio.sh — Sync prod to portfolio repo (without secrets)
rsync -av \
  --exclude '.env' \
  --exclude '*.db' \
  --exclude 'storage/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'state/' \
  --exclude 'nohup.out' \
  --exclude '__pycache__/' \
  --exclude 'venv/' \
  --exclude 'chrome_profile_*' \
  --exclude '*.tar.gz' \
  --exclude 'bb' \
  --exclude 'config.json' \
  /volume1/automation/ /volume1/portfolio/

echo "Synced. Now cd ~/portfolio && git status"
```

---

## Gotchas & Tips

- **Always dry run first** when using `--delete`: `rsync -avn --delete source/ dest/`
- **Trailing slash matters** — `source/` copies contents, `source` copies the folder itself
- **rsync over SSH is encrypted** — safe for sensitive files
- **Resume interrupted transfers** with `-P` (partial + progress)
- **Bandwidth limit** for slow connections: `--bwlimit=1000` (KB/s)

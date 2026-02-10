# Portfolio Workflow — Prod → Repo Buffer

The safe workflow for maintaining a public GitHub portfolio from a live production environment.

---

## The Setup

```
/volume1/automation/    ← PRODUCTION (live code, running 24/7, has .env files)
/volume1/portfolio/     ← GIT REPO (clean code, pushed to GitHub, NO secrets)
```

**Golden rule:** Never run `git init` or `git push` from the production folder. Always copy to portfolio first.

---

## One-Time Setup

```bash
# Create the portfolio repo
cd /volume1/portfolio
git init
git remote add origin https://github.com/yourusername/your-repo.git

# Create .gitignore FIRST (before adding any files)
nano .gitignore
# (paste the template from the gitignore guide)

# Create .env.example files for each project
# Initial commit
git add .
git commit -m "Initial commit"
git push -u origin main
```

---

## The Update Routine

### Option A: Copy individual files
When you've fixed a bug or added a feature to one file:

```bash
# 1. Copy the modified file
cp /volume1/automation/trackers/core/torrentleech.py \
   /volume1/portfolio/trackers/core/torrentleech.py

# 2. Go to repo and check
cd /volume1/portfolio
git status                      # Shows what changed
git diff                        # Review the actual changes

# 3. Commit and push
git add trackers/core/torrentleech.py
git commit -m "Fix: TorrentLeech matching false positives"
git push
```

### Option B: Rsync batch sync
When you've made changes across many files:

```bash
# 1. Sync (excluding secrets, data, logs)
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

# 2. Review what changed
cd /volume1/portfolio
git status
git diff

# 3. Commit
git add .
git commit -m "Update: batch sync - improved matching + new Discord embeds"
git push
```

---

## Pre-Push Security Checklist

Run this EVERY TIME before `git push`:

```bash
cd /volume1/portfolio

# 1. Check for secrets in staged files
grep -rniE "token|password|api_key|webhook|secret" --include="*.py" --include="*.yml" --include="*.json" --include="*.sh" .

# 2. Check for .env files
find . -name ".env" -o -name "*.env"

# 3. Check for cookie/session files
find . -name "*cookie*" -o -name "*session*"

# 4. Check for database files
find . -name "*.db"

# 5. Review what's about to be committed
git diff --staged --name-only
```

**Quick one-liner version:**
```bash
cd /volume1/portfolio && \
  echo "=== Secrets ===" && grep -rniE "token|password|api_key|webhook|secret" --include="*.py" --include="*.yml" --include="*.json" . | grep -v "getenv\|environ\|\.example\|\.gitignore" && \
  echo "=== Env files ===" && find . -name ".env" -not -name ".env.example" && \
  echo "=== Done ==="
```

---

## Good Commit Message Patterns

```bash
# Feature
git commit -m "Add: Added new feature"

# Bug fix
git commit -m "Fix: false positive on torrents with season packs"

# Update/improvement
git commit -m "Update: bandwidth manager threshold to 80 Mbps"

# Refactor (no behavior change)
git commit -m "Refactor: extract Discord notification into separate module"

# Documentation
git commit -m "Docs: add README for tracker automation stack"

# Cleanup
git commit -m "Remove: deprecated chrome profile cleanup"
```

---

## Handling Sensitive Defaults in Code

When copying code to your portfolio, watch out for hardcoded fallback values:

**Good:**
```python
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    raise ValueError("DISCORD_WEBHOOK_URL environment variable is required")
```

Or for optional features:
```python
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# Notifications disabled if webhook not configured
```

---

## If You Accidentally Push Secrets

Act fast:
```bash
# 1. Immediately rotate the exposed credentials
#    (change passwords, regenerate tokens/API keys/webhooks)

# 2. Remove the file from tracking
git rm --cached .env
echo ".env" >> .gitignore
git add .gitignore
git commit -m "Remove accidentally committed secrets"
git push

# 3. Clean git history (secrets are still in old commits!)
pip install git-filter-repo
git filter-repo --path .env --invert-paths
git push --force

# 4. Verify
git log --all --full-history -- .env    # Should show nothing
```

**The most important step is #1.** Even after removing from git, bots scrape GitHub in real-time for exposed secrets. Rotate immediately.

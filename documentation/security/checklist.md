# Security Checklist

Best practices for keeping secrets out of your code and repos.

---

## The .env Pattern

**Rule:** Secrets go in `.env` files. Code reads them with `os.getenv()`. The `.env` file is NEVER committed.

```
project/
├── .env              ← Real secrets (gitignored)
├── .env.example      ← Template with placeholders (committed)
├── .gitignore        ← Contains ".env"
├── config.py         ← Reads from os.getenv()
└── app.py
```

**.env:**
```
DISCORD_TOKEN=MTQ2MDI1NjgxOTIy...
API_KEY=abc123def1337
```

**.env.example:**
```
DISCORD_TOKEN=your_discord_bot_token
API_KEY=your_api_key_here
```

**config.py:**
```python
import os
from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("API_KEY")
```

---

## Pre-Push Audit

Run these checks before every `git push`:

```bash
cd /volume1/portfolio

# 1. Scan for secrets in code
grep -rniE "token|password|api_key|webhook|secret" \
  --include="*.py" --include="*.yml" --include="*.json" --include="*.sh" . \
  | grep -v "getenv\|environ\|example\|gitignore\|SKILL\|README"

# 2. Check for .env files
find . -name ".env" -not -name ".env.example"

# 3. Check for database files
find . -name "*.db"

# 4. Check for cookie/session files
find . -name "*cookie*" -o -name "*session*"

# 5. Check for large binary files
find . -type f -size +1M

# 6. Review staged changes
git diff --staged --name-only
```

---

## Common Mistakes

### Hardcoded fallback values
```python
# BAD — secret in source code
WEBHOOK = os.getenv("WEBHOOK", "https://discord.com/api/webhooks/REAL_TOKEN")

# GOOD — fail if missing
WEBHOOK = os.environ["WEBHOOK"]

# GOOD — optional with safe default
WEBHOOK = os.getenv("WEBHOOK")  # None if not set
```

### Secrets in docker-compose.yml
```yaml
# BAD — secret visible in file
environment:
  - DISCORD_WEBHOOK=https://discord.com/api/webhooks/real_token

# GOOD — reference .env file
env_file:
  - .env

# GOOD — variable substitution
environment:
  - DISCORD_WEBHOOK=${DISCORD_WEBHOOK}
```

### Committing config files with API keys
```json
// BAD — config.json with real key
{"apiKey": "893f1e7582d3b6bb7371250bd3e69ef0"}
```
Add `config.json` to `.gitignore` and create `config.json.example`.

---

## What Should Be in .gitignore

```gitignore
# Secrets
.env
*.env
config.json

# Session data
*cookie*.json
*session*

# Databases
*.db

# Logs
*.log
logs/
nohup.out

# Python
__pycache__/
venv/

# Data/state
storage/
state/
data/
```

---

## If Secrets Get Leaked

1. **Rotate immediately** — change the password/regenerate the token/key
2. **Remove from tracking** — `git rm --cached .env && git commit`
3. **Clean history** — `git filter-repo --path .env --invert-paths`
4. **Force push** — `git push --force`
5. **Verify** — `git log --all -- .env` should show nothing

The rotation is the most important step. GitHub is constantly scraped by bots looking for leaked secrets.

---

## Password Hygiene

- Use a unique password per service
- Use a password manager (Bitwarden, KeePass, 1Password)
- Don't reuse your tracker password for qBittorrent or other services
- For API keys, regenerate them periodically in each service's settings

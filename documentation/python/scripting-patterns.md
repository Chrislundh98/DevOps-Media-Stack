# Python Scripting Patterns

Common patterns for automation scripts — logging, env vars, CLI args, file I/O.

---

## Environment Variables (python-dotenv)

```python
import os
from dotenv import load_dotenv

load_dotenv()  # Loads from .env file in current directory

# Required variable (fail if missing)
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# Optional variable with default
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))

# Validate early
if not os.getenv("API_KEY"):
    raise ValueError("API_KEY environment variable is required")
```

---

## Logging (Don't Use print())

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logger.info("Starting monitor...")
logger.warning("Rate limit approaching")
logger.error("Failed to connect: %s", error)
logger.debug("Response payload: %s", data)
```

**Log to file AND console:**
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/logs/app.log"),
    ],
)
```

---

## Command-Line Arguments

```python
import argparse

parser = argparse.ArgumentParser(description="Torrent monitor")
parser.add_argument("--once", action="store_true", help="Run once and exit")
parser.add_argument("--interval", type=int, default=900, help="Seconds between checks")
parser.add_argument("--dry-run", action="store_true", help="Don't actually download")
args = parser.parse_args()

if args.once:
    run_once()
else:
    run_loop(interval=args.interval)
```

---

## File I/O

**JSON (most common for automation):**
```python
import json

# Read
with open("data.json", "r") as f:
    data = json.load(f)

# Write
with open("data.json", "w") as f:
    json.dump(data, f, indent=2)
```

**Path handling with pathlib:**
```python
from pathlib import Path

config_dir = Path("/app/config")
config_dir.mkdir(parents=True, exist_ok=True)

config_file = config_dir / "settings.json"
if config_file.exists():
    data = json.loads(config_file.read_text())

# List files
for py_file in Path("/app").rglob("*.py"):
    print(py_file)
```

---

## HTTP Requests

```python
import requests

# GET
response = requests.get("https://api.example.com/data", timeout=30)
response.raise_for_status()
data = response.json()

# POST JSON (e.g., Discord webhook)
requests.post(webhook_url, json={"content": "Hello!"}, timeout=10)

# POST form data (e.g., login)
requests.post(url, data={"username": user, "password": pw}, timeout=10)

# With headers
requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)

# Session (keeps cookies across requests)
session = requests.Session()
session.post("https://example.com/login", data=creds)
response = session.get("https://example.com/dashboard")
```

---

## Error Handling & Retry

```python
import time

def fetch_with_retry(url, max_retries=3, delay=5):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning("Attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))  # Increasing delay
    logger.error("All %d attempts failed for %s", max_retries, url)
    return None
```

---

## Main Loop Pattern (for Monitors/Bots)

```python
import time
import sys

def main_loop():
    logger.info("Starting monitor (interval: %ds)", CHECK_INTERVAL)

    while True:
        try:
            check()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            sys.exit(0)
        except Exception as e:
            logger.exception("Unexpected error: %s", e)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main_loop()
```

---

## Discord Webhook Helper

```python
import requests

def send_discord(webhook_url, title, description, color=0x00FF00):
    """Send a Discord embed notification."""
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
        }]
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("Discord notification failed: %s", e)
```

---

## Script Template

Copy-paste starter for new automation scripts:

```python
#!/usr/bin/env python3
"""Short description of what this script does."""

import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# Config
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def check():
    """Main logic goes here."""
    logger.info("Running check...")
    # TODO: implement


def main():
    logger.info("Starting...")
    while True:
        try:
            check()
        except KeyboardInterrupt:
            logger.info("Stopped.")
            sys.exit(0)
        except Exception as e:
            logger.exception("Error: %s", e)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
```

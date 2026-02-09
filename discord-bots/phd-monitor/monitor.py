#!/usr/bin/env python3
"""
PhD Application Monitor for Cybercampus Sweden
Monitors university listings for when application links become active.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Configuration
TARGET_URL = "https://www.cybercampus.se/en/research/cybercampus-graduate"
CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes
STATE_FILE = Path("/app/state/state.json")
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
)

# Universities to monitor - add more as needed
MONITORED_UNIVERSITIES = [
    "Jönköping University",
]

# For testing: set via environment variable
# TEST_MODE=karlstad will monitor Karlstad University instead (which has an active link)
TEST_MODE = os.environ.get("TEST_MODE", "").lower()

if TEST_MODE == "karlstad":
    MONITORED_UNIVERSITIES = ["Karlstad University"]
    logging.info("TEST MODE: Monitoring Karlstad University (has active link)")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class UniversityStatus:
    """Represents the status of a university listing."""
    PLAINTEXT = "plaintext"      # No link yet - application not open
    LINK_ACTIVE = "link_active"  # Has link - application is open!
    FILLED = "filled"            # Position already filled
    NOT_FOUND = "not_found"      # University not found on page


def load_state() -> dict:
    """Load previous state from file."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load state file: {e}")
    return {"universities": {}}


def save_state(state: dict) -> None:
    """Save current state to file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_page() -> Optional[str]:
    """Fetch the Cybercampus page HTML."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PhD-Monitor/1.0; educational purpose)"
        }
        response = requests.get(TARGET_URL, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Failed to fetch page: {e}")
        return None


def parse_university_status(html: str, university_name: str) -> tuple[str, Optional[str]]:
    """
    Parse the status of a specific university from the HTML.
    
    Returns:
        tuple: (status, url) where url is None if no link exists
    """
    soup = BeautifulSoup(html, "html.parser")
    
    # Find all list items
    for li in soup.find_all("li"):
        li_text = li.get_text(strip=True)
        
        # Check if this li contains our university
        if university_name.lower() in li_text.lower():
            # Check if position is filled
            if "position already filled" in li_text.lower():
                return UniversityStatus.FILLED, None
            
            # Check if there's a link
            link = li.find("a")
            if link and link.get("href"):
                return UniversityStatus.LINK_ACTIVE, link.get("href")
            
            # Plain text - no link yet
            return UniversityStatus.PLAINTEXT, None
    
    return UniversityStatus.NOT_FOUND, None


def send_discord_notification(
    university: str, 
    old_status: str, 
    new_status: str, 
    url: Optional[str] = None
) -> bool:
    """Send a Discord webhook notification."""
    
    # Determine embed color and message based on status change
    if new_status == UniversityStatus.LINK_ACTIVE:
        color = 0x00FF00  # Green - application open!
        title = "🎉 PhD Application Now Open!"
        description = f"**{university}** application link is now active!"
        if url:
            description += f"\n\n**Apply here:** {url}"
    elif new_status == UniversityStatus.FILLED:
        color = 0xFF0000  # Red - position filled
        title = "❌ Position Filled"
        description = f"**{university}** position has been filled."
    else:
        color = 0xFFFF00  # Yellow - other change
        title = "📝 Status Changed"
        description = f"**{university}** status changed from `{old_status}` to `{new_status}`"
    
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "Cybercampus PhD Monitor"
        },
        "fields": [
            {
                "name": "Previous Status",
                "value": old_status or "Unknown",
                "inline": True
            },
            {
                "name": "New Status",
                "value": new_status,
                "inline": True
            }
        ]
    }
    
    if url:
        embed["url"] = url
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Discord notification sent successfully for {university}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Discord notification: {e}")
        return False


def check_universities() -> None:
    """Main check function - fetches page and checks all monitored universities."""
    logger.info("Starting check...")
    
    html = fetch_page()
    if not html:
        logger.error("Could not fetch page, skipping this check")
        return
    
    state = load_state()
    
    for university in MONITORED_UNIVERSITIES:
        logger.info(f"Checking {university}...")
        
        current_status, url = parse_university_status(html, university)
        previous_status = state["universities"].get(university, {}).get("status")
        
        logger.info(f"  Status: {current_status}" + (f" (URL: {url})" if url else ""))
        
        # Check if status changed
        if previous_status is None:
            # First run - just record the status
            logger.info(f"  First check - recording initial status: {current_status}")
        elif current_status != previous_status:
            logger.info(f"  STATUS CHANGED: {previous_status} -> {current_status}")
            send_discord_notification(university, previous_status, current_status, url)
        else:
            logger.info(f"  No change from previous status")
        
        # Update state
        state["universities"][university] = {
            "status": current_status,
            "url": url,
            "last_checked": datetime.utcnow().isoformat(),
            "last_changed": state["universities"].get(university, {}).get("last_changed")
        }
        
        if current_status != previous_status:
            state["universities"][university]["last_changed"] = datetime.utcnow().isoformat()
    
    save_state(state)
    logger.info("Check complete")


def run_once() -> None:
    """Run a single check (for testing)."""
    check_universities()


def run_loop() -> None:
    """Run continuous monitoring loop."""
    logger.info("=" * 50)
    logger.info("PhD Application Monitor Starting")
    logger.info(f"Monitoring: {', '.join(MONITORED_UNIVERSITIES)}")
    logger.info(f"Check interval: {CHECK_INTERVAL_SECONDS // 60} minutes")
    logger.info(f"Target URL: {TARGET_URL}")
    logger.info("=" * 50)
    
    while True:
        try:
            check_universities()
        except Exception as e:
            logger.exception(f"Unexpected error during check: {e}")
        
        logger.info(f"Next check in {CHECK_INTERVAL_SECONDS // 60} minutes...")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        run_loop()
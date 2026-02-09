#!/usr/bin/env python3
"""
DigitalCore.club Daily Stats Reporter
Scrapes user profile page (including HnR count from status bar) using cookies and sends stats to Discord.
"""
import logging
import os
import re
import time
import shutil
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- Paths ---
BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / 'logs' / 'digitalcore' / 'stats'
STORAGE_DIR = BASE_DIR / 'storage'
JSON_DIR = STORAGE_DIR / 'json'
COOKIE_FILE_PATH = JSON_DIR / 'dc_cookie.json'
CHROME_DRIVER_LOG = LOG_DIR / 'chromedriver_dc_stats.log'
CHROME_PROFILE_DIR = STORAGE_DIR / 'chrome_profile_dc_stats'
ACCURACY_LOG_FILE = JSON_DIR / 'accuracy_log_dc.json'

LOG_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR.parent / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'digitalcore_stats_reporter.log'),
        logging.StreamHandler()
    ]
)

# --- Configuration ---
BASE_URL = "https://digitalcore.club"
PROFILE_URL = "https://digitalcore.club/user/USER_ID/your-username"
MAX_RETRIES = 3

# --- Selectors ---
LOGGED_IN_PROOF_SELECTOR = (By.CSS_SELECTOR, "div.table-responsive[ng-show='vm.user']")
UPLOADED_SELECTOR = (By.XPATH, "//td[contains(text(), 'Uploaded')]/following-sibling::td")
DOWNLOADED_SELECTOR = (By.XPATH, "//td[contains(text(), 'Downloaded')]/following-sibling::td")
RATIO_SELECTOR = (By.XPATH, "//td[contains(text(), 'Ratio')]/following-sibling::td/span")
LEECHBONUS_SELECTOR = (By.XPATH, "//td[contains(text(), 'Leech Bonus')]/following-sibling::td//div[contains(@class, 'progress-bar')]")
BONUS_POINTS_VALUE_SELECTOR = (By.XPATH, "//td[contains(text(), 'Bonus Points')]/following-sibling::td/b")
BONUS_POINTS_DETAILS_SELECTOR = (By.XPATH, "//td[contains(text(), 'Bonus Points')]/following-sibling::td/span")
TRANSFER_STATS_SELECTOR = (By.XPATH, "//span[@translate='USER.PEER_ACTIVITY']")
HNR_COUNT_SELECTOR = (By.XPATH, "//div[@id='statusBox']//a[@href='/hnr']/following-sibling::b")


class DCStatsReporter:
    def __init__(self):
        self.driver = None
        self.webhook_url = os.getenv("DISCORD_STATS_HOOK")

    def _find_chrome_binary(self):
        possible_paths = [
            '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/chrome',
            '/opt/google/chrome/google-chrome', '/snap/bin/chromium',
            shutil.which('google-chrome'), shutil.which('chromium'),
            shutil.which('chromium-browser'), shutil.which('chrome')
        ]
        for path in possible_paths:
            if path and os.path.exists(path):
                logging.info(f"Found Chrome binary at: {path}")
                return path
        logging.warning("Could not find Chrome binary automatically.")
        return None

    def setup_chrome_driver(self):
        logging.info("Setting up standard Chrome driver...")
        try:
            chrome_options = Options()
            chrome_binary = self._find_chrome_binary()
            if chrome_binary:
                chrome_options.binary_location = chrome_binary
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-software-rasterizer")
            chrome_options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")

            logging.info("Installing/updating ChromeDriver via webdriver_manager...")
            service = Service(
                ChromeDriverManager().install(),
                log_output=str(CHROME_DRIVER_LOG)
            )
            logging.info("Starting Chrome browser...")
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.set_page_load_timeout(120)
            logging.info("Chrome driver created successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to create Chrome driver: {e}", exc_info=True)
            return False

    def _load_cookies(self):
        logging.info(f"Loading cookies from {COOKIE_FILE_PATH}...")
        if not COOKIE_FILE_PATH.exists():
            logging.error(f"Cookie file not found: {COOKIE_FILE_PATH}")
            return False
        try:
            with open(COOKIE_FILE_PATH, 'r') as f:
                cookies = json.load(f)
            if not cookies:
                logging.error("Cookie file is empty.")
                return False
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode cookie JSON: {e}")
            return False

        added = 0
        for cookie in cookies:
            if "name" not in cookie or "value" not in cookie or cookie["name"] == "":
                continue
            if 'expirationDate' in cookie:
                cookie['expires'] = int(cookie.pop('expirationDate'))
            elif 'expiry' in cookie:
                cookie['expires'] = int(cookie.pop('expiry'))
            cookie.pop('hostOnly', None)
            cookie.pop('storeId', None)
            cookie.pop('session', None)
            if 'sameSite' in cookie and cookie['sameSite'] not in ['Strict', 'Lax', 'None']:
                cookie['sameSite'] = 'Lax'
            try:
                self.driver.add_cookie(cookie)
                added += 1
            except Exception as e:
                logging.warning(f"Could not add cookie {cookie.get('name')}: {e}")

        logging.info(f"Loaded {added} cookies.")
        return added > 0

    def login_with_cookies(self):
        logging.info("Logging into DigitalCore using cookies")
        try:
            self.driver.get(BASE_URL)
            time.sleep(3)
            if not self._load_cookies():
                logging.error("Failed to load cookies.")
                return False

            self.driver.get(PROFILE_URL)
            time.sleep(5)

            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'logout')]"))
                )
                logging.info("Successfully logged into DigitalCore!")
                return True
            except TimeoutException:
                try:
                    self.driver.find_element(By.NAME, "username")
                    logging.error("Login failed - found login form. Cookies invalid.")
                    return False
                except NoSuchElementException:
                    logging.info("Login verification OK (no login form found).")
                    return True
        except Exception as e:
            logging.error(f"Error during login: {e}", exc_info=True)
            return False

    def _load_page_with_retry(self, url, max_retries=MAX_RETRIES):
        for attempt in range(max_retries):
            try:
                self.driver.get(url)
                return True
            except TimeoutException:
                if attempt < max_retries - 1:
                    logging.warning(f"Page load timeout (attempt {attempt+1}/{max_retries}). Retrying...")
                    time.sleep(4)
                else:
                    logging.error(f"Failed to load page after {max_retries} attempts: {url}")
                    return False
            except WebDriverException as e:
                logging.error(f"WebDriver error loading page: {e}")
                return False
        return False

    def _get_element_text_safe(self, selector, wait_time=10):
        try:
            element = WebDriverWait(self.driver, wait_time).until(EC.visibility_of_element_located(selector))
            return element.text.strip()
        except (NoSuchElementException, TimeoutException):
            return "N/A"
        except Exception as e:
            logging.error(f"Error getting text for {selector}: {e}")
            return "N/A"

    def _get_element_attribute_safe(self, selector, attribute, wait_time=10):
        try:
            element = WebDriverWait(self.driver, wait_time).until(EC.visibility_of_element_located(selector))
            return element.get_attribute(attribute) or "N/A"
        except (NoSuchElementException, TimeoutException):
            return "N/A"
        except Exception as e:
            logging.error(f"Error getting attribute '{attribute}' for {selector}: {e}")
            return "N/A"

    def _get_match_accuracy(self):
        if not ACCURACY_LOG_FILE.exists():
            return {'all_time': 'N/A', 'last_30_days': 'N/A', 'last_7_days': 'N/A', 'trend': '➡️', 'trend_text': 'No data'}

        try:
            with open(ACCURACY_LOG_FILE, 'r') as f:
                data = json.load(f)

            total_a = data.get("total_attempts", 0)
            total_s = data.get("total_successes", 0)
            all_time = f"{(total_s / total_a) * 100:.1f}%" if total_a > 0 else "N/A"

            sessions = data.get('sessions', [])
            if not sessions:
                return {'all_time': all_time, 'last_30_days': 'N/A', 'last_7_days': 'N/A', 'trend': '➡️', 'trend_text': 'No sessions'}

            now = datetime.now()
            s30, s7, s14_7 = [], [], []
            for s in sessions:
                try:
                    t = datetime.fromisoformat(s['timestamp'])
                    if t >= now - timedelta(days=7):
                        s7.append(s); s30.append(s)
                    elif t >= now - timedelta(days=14):
                        s14_7.append(s); s30.append(s)
                    elif t >= now - timedelta(days=30):
                        s30.append(s)
                except:
                    continue

            def calc(ss):
                ta = sum(x.get('attempts', 0) for x in ss)
                ts = sum(x.get('successes', 0) for x in ss)
                return (ta, ts, f"{(ts/ta)*100:.1f}%" if ta > 0 else "N/A")

            t7, su7, a7 = calc(s7)
            _, _, a30 = calc(s30)

            trend_emoji, trend_text = '➡️', 'Stable'
            if s7 and s14_7:
                tp, sp, _ = calc(s14_7)
                if t7 > 0 and tp > 0:
                    diff = (su7/t7)*100 - (sp/tp)*100
                    if diff > 2: trend_emoji, trend_text = '📈', f'+{diff:.1f}%'
                    elif diff < -2: trend_emoji, trend_text = '📉', f'{diff:.1f}%'
                    else: trend_emoji, trend_text = '➡️', f'{diff:+.1f}%'

            return {'all_time': all_time, 'last_30_days': a30, 'last_7_days': a7, 'trend': trend_emoji, 'trend_text': trend_text}
        except Exception as e:
            logging.error(f"Failed to calculate match accuracy: {e}")
            return {'all_time': 'Error', 'last_30_days': 'Error', 'last_7_days': 'Error', 'trend': '❌', 'trend_text': 'Error'}

    def _send_discord_notification(self, stats, hnr_count, match_accuracy_dict):
        if not self.webhook_url:
            logging.error("DISCORD_STATS_HOOK not set.")
            return

        title = "📊 Daily DigitalCore Stats Report"
        color = 3066993

        hnr_count_int = -1
        try:
            hnr_count_int = int(hnr_count)
            if hnr_count_int > 0:
                title = f"🚨 HnR WARNING ({hnr_count_int}) 🚨"
                color = 15158332
        except (ValueError, TypeError):
            pass

        parts = [
            f"**⬆️ Uploaded:** `{stats.get('uploaded', 'N/A')}`",
            f"**⬇️ Downloaded:** `{stats.get('downloaded', 'N/A')}`",
            f"**📈 Ratio:** `{stats.get('ratio', 'N/A')}`", "",
            "**🎯 Match Accuracy:**",
            f"  • 7-Day: `{match_accuracy_dict['last_7_days']}` {match_accuracy_dict['trend']} `{match_accuracy_dict['trend_text']}`",
            f"  • 30-Day: `{match_accuracy_dict['last_30_days']}`",
            f"  • All-Time: `{match_accuracy_dict['all_time']}`", "",
            f"**✨ Leech Bonus:** `{stats.get('leech_bonus', 'N/A')}%`",
            f"**💰 Bonus Points:** `{stats.get('bonus_points_value', 'N/A')}`",
            f"   *Details:* `{stats.get('bonus_points_details', 'N/A')}`", "",
            f"**🔄 Transfers:** `{stats.get('transfer_stats', 'N/A')}`",
        ]

        if hnr_count_int > 0:
            parts += ["", f"**🚨 Hit & Runs:** `{hnr_count_int}`"]
        elif hnr_count == "N/A" or hnr_count_int == -1:
            parts += ["", "**⚠️ Hit & Runs:** `Check Failed`"]

        payload = {
            "username": "DC Stats Bot",
            "embeds": [{"title": title, "description": "\n".join(parts), "color": color}]
        }

        logging.info("Sending DC stats notification to Discord...")
        try:
            requests.post(self.webhook_url, json=payload).raise_for_status()
            logging.info("Discord notification sent!")
        except Exception as e:
            logging.error(f"Failed to send Discord notification: {e}")

    def run(self):
        if not self.setup_chrome_driver():
            return

        try:
            if not self.login_with_cookies():
                return

            logging.info(f"Navigating to profile: {PROFILE_URL}")
            if not self._load_page_with_retry(PROFILE_URL):
                return

            try:
                WebDriverWait(self.driver, 20).until(EC.visibility_of_element_located(LOGGED_IN_PROOF_SELECTOR))
                logging.info("Profile page loaded.")
            except TimeoutException:
                logging.error("Profile page failed to load.")
                return

            logging.info("Scraping profile statistics...")
            stats = {}

            # HnR count - check /hnr page directly (statusBox selector is unreliable)
            hnr_count = 0
            try:
                self.driver.get("https://digitalcore.club/hnr")
                time.sleep(2)
                try:
                    hnr_table = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "table[ng-show='vm.snatchLoghnr']"))
                    )
                    hnr_rows = self.driver.find_elements(By.CSS_SELECTOR, "tr[ng-repeat^='snatch in vm.snatchLoghnr']")
                    hnr_count = len(hnr_rows)
                    if hnr_count > 0:
                        logging.warning(f"HNR_DETECTED: Found {hnr_count} Hit & Runs.")
                    else:
                        logging.info("HnR count: 0")
                except TimeoutException:
                    logging.info("No HnR table found - assuming 0 HnRs")
                    hnr_count = 0
            except Exception as e:
                logging.error(f"Error checking HnR page: {e}")
                hnr_count = "N/A"

            # Navigate back to profile for other stats
            if not self._load_page_with_retry(PROFILE_URL):
                return
            try:
                WebDriverWait(self.driver, 20).until(EC.visibility_of_element_located(LOGGED_IN_PROOF_SELECTOR))
            except TimeoutException:
                logging.error("Failed to reload profile page")
                return

            stats['uploaded'] = self._get_element_text_safe(UPLOADED_SELECTOR)
            stats['downloaded'] = self._get_element_text_safe(DOWNLOADED_SELECTOR)
            stats['ratio'] = self._get_element_text_safe(RATIO_SELECTOR)
            stats['leech_bonus'] = self._get_element_attribute_safe(LEECHBONUS_SELECTOR, 'aria-valuenow')
            stats['bonus_points_value'] = self._get_element_text_safe(BONUS_POINTS_VALUE_SELECTOR)
            stats['bonus_points_details'] = self._get_element_text_safe(BONUS_POINTS_DETAILS_SELECTOR)
            stats['transfer_stats'] = self._get_element_text_safe(TRANSFER_STATS_SELECTOR)

            logging.info(f"Stats scraped: {stats} | HnR Count: {hnr_count}")

            match_accuracy = self._get_match_accuracy()
            self._send_discord_notification(stats, hnr_count, match_accuracy)

        except Exception as e:
            logging.error(f"Unhandled error: {e}", exc_info=True)
        finally:
            if self.driver:
                time.sleep(1)
                self.driver.quit()
            logging.info("Driver quit.")


if __name__ == "__main__":
    reporter = DCStatsReporter()
    reporter.run()

#!/usr/bin/env python3
"""
TorrentLeech "Big Boys" Monitor - BYPASS EDITION
- Checks qBittorrent category 'big_boys'.
- If empty: Scrapes TL for the smallest torrent >= 1TiB (with 8+ seeders), downloads it.
- If occupied: Checks TL Seeding page. If seeding time >= 8 days, removes it to make room for the next.
"""

import logging
import os
import sys
import time
import requests
import re
import json
import random
from datetime import datetime
from dotenv import load_dotenv

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from qbittorrentapi import Client, APIConnectionError

# --- Setup Paths & Config ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, 'logs', 'torrentleech', 'big_boys')
STORAGE_DIR_TXT = os.path.join(BASE_DIR, 'storage', 'txt')
STORAGE_DIR_JSON = os.path.join(BASE_DIR, 'storage', 'json')

# Ensure directories exist
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STORAGE_DIR_TXT, exist_ok=True)
os.makedirs(STORAGE_DIR_JSON, exist_ok=True)

# Files
HISTORY_FILE = os.path.join(STORAGE_DIR_TXT, "big_boys_history.txt")
ACTIVE_FILE = os.path.join(STORAGE_DIR_JSON, "big_boys_active.json")
COOKIE_FILE = os.path.join(STORAGE_DIR_JSON, "tl_cookie.json")
DEBUG_SCREENSHOT_PATH = os.path.join(LOG_DIR, "big_boys_debug.png")

# URLs
START_URL = "https://www.torrentleech.org/" # Root for context
BROWSE_URL = "https://www.torrentleech.org/torrents/browse/index/orderby/size/order/desc"
# Dynamic seeding URL based on username (handled in __init__)
SEEDING_URL_TEMPLATE = "https://www.torrentleech.org/profile/{user}/seeding"

# Config
TARGET_CATEGORY = "big_boys"
MIN_SEEDERS = 8
MIN_SIZE_BYTES = 1024 * 1024 * 1024 * 1024  # 1 TiB
TARGET_DAYS = 8

# Selectors
LOGGED_IN_PROOF_SELECTOR = (By.CSS_SELECTOR, "a[href*='/user/profile']")
TORRENT_TABLE_SELECTOR = (By.CSS_SELECTOR, "table.torrents")
TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "table.torrents tbody tr.torrent")
SEEDING_TABLE_SELECTOR = (By.ID, "profile-seedingTable")
SEEDING_ROWS_SELECTOR = (By.CSS_SELECTOR, "table#profile-seedingTable tbody tr")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'big_boys_monitor.log')),
        logging.StreamHandler()
    ]
)

load_dotenv(os.path.join(BASE_DIR, '.env'))

class BigBoyHunter:
    def __init__(self):
        self.driver = None
        self.session = requests.Session()
        self.qbt_client = None
        
        # Credentials
        self.username = os.getenv("USERNAME")
        self.password = os.getenv("PASSWORD")
        self.qbit_url = os.getenv("QBIT_URL")
        self.qbit_user = os.getenv("QBIT_USER")
        self.qbit_pass = os.getenv("QBIT_PASS")
        
        if not self.username:
             logging.critical("USERNAME not found in .env!")
             sys.exit(1)

        self.seeding_url = SEEDING_URL_TEMPLATE.format(user=self.username)

    # --- BYPASS HELPERS ---
    def _load_config_from_json(self):
        if not os.path.exists(COOKIE_FILE):
            logging.error(f"Cookie file not found: {COOKIE_FILE}")
            return [], None
        try:
            with open(COOKIE_FILE, 'r') as f:
                data = json.load(f)
            user_agent = None
            cookies = []
            for item in data:
                if "user_agent" in item:
                    user_agent = item["user_agent"]
                else:
                    cookies.append(item)
            return cookies, user_agent
        except Exception as e:
            logging.error(f"Failed to load JSON config: {e}")
            return [], None

    def _attempt_click_turnstile(self):
        try:
            shadow_script = """
            let target = document.querySelector("input[type='checkbox']");
            if (!target) {
                document.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        let cb = el.shadowRoot.querySelector("input[type='checkbox']");
                        if (cb) target = cb;
                    }
                });
            }
            return target;
            """
            element = self.driver.execute_script(shadow_script)
            if element:
                logging.info("  >> Turnstile checkbox found. Clicking...")
                action = ActionChains(self.driver)
                action.move_by_offset(random.randint(10, 100), random.randint(10, 100))
                action.perform()
                time.sleep(0.5)
                element.click()
                return True
            
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                if "challenges" in iframe.get_attribute("src"):
                    self.driver.switch_to.frame(iframe)
                    cb = self.driver.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                    if cb:
                        cb.click()
                        self.driver.switch_to.default_content()
                        return True
                    self.driver.switch_to.default_content()
        except: pass
        return False

    def _handle_cloudflare(self):
        if "Just a moment" not in self.driver.title and "challenge" not in self.driver.page_source.lower():
            return True
        logging.warning("⚠️ Cloudflare Challenge Detected! Solving...")
        start = time.time()
        while time.time() - start < 45:
            if "Just a moment" not in self.driver.title:
                logging.info("✓ Cloudflare bypassed.")
                return True
            self._attempt_click_turnstile()
            time.sleep(3)
        logging.error("❌ Cloudflare timed out.")
        try: self.driver.save_screenshot(DEBUG_SCREENSHOT_PATH)
        except: pass
        return False

    def _init_driver(self):
        """Initializes Undetected Chrome Driver with spoofed UA."""
        logging.info("Initializing Chrome options...")
        cookies, user_agent = self._load_config_from_json()
        
        self.cookies_to_inject = cookies if cookies else []
        
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--force-device-scale-factor=1")
            
            if user_agent:
                chrome_options.add_argument(f"--user-agent={user_agent}")
            
            self.driver = uc.Chrome(options=chrome_options, version_main=141)
            self.driver.set_page_load_timeout(90)
            logging.info("Chrome driver initialized.")
        except Exception as e:
            logging.error(f"Failed to initialize driver: {e}")
            sys.exit(1)

    def _init_qbit(self):
        """Connects to qBittorrent."""
        try:
            self.qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            self.qbt_client.auth_log_in()
            logging.info(f"Connected to qBittorrent {self.qbt_client.app.version}")
            
            cats = self.qbt_client.torrents_categories()
            if TARGET_CATEGORY not in cats:
                logging.info(f"Creating category '{TARGET_CATEGORY}'")
                self.qbt_client.torrents_create_category(name=TARGET_CATEGORY)
                
        except APIConnectionError as e:
            logging.error(f"qBittorrent connection failed: {e}")
            if self.driver: self.driver.quit()
            sys.exit(1)

    def _login_tl(self):
        """Bypass login using manual cookies and transfer to session."""
        logging.info("Performing Login Bypass...")
        
        try:
            # 1. Root Context
            try: self.driver.get("https://www.torrentleech.org/robots.txt")
            except: pass
            
            # 2. Inject
            logging.info("Injecting cookies...")
            for c in self.cookies_to_inject:
                c_dict = {'name': c['name'], 'value': c['value'], 'domain': c.get('domain', '.torrentleech.org'), 'path': '/', 'secure': c.get('secure', True)}
                if not c_dict['domain'].startswith('.'): c_dict['domain'] = '.' + c_dict['domain']
                try: self.driver.add_cookie(c_dict)
                except: pass
            
            # 3. Load Root & Solve
            logging.info("Navigating to dashboard...")
            self.driver.get(START_URL)
            if not self._handle_cloudflare():
                logging.error("Cloudflare failed.")
                sys.exit(1)
            
            # 4. Verify
            if "login" in self.driver.current_url and "user" not in self.driver.current_url:
                 logging.error("❌ Cookie Login Failed. Redirected to Login.")
                 self.driver.save_screenshot(DEBUG_SCREENSHOT_PATH)
                 sys.exit(1)
            
            logging.info("✓ Logged in.")

            # 5. Transfer to Requests Session
            self.session.cookies.clear()
            for cookie in self.driver.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
            logging.info("Session cookies synced.")

        except Exception as e:
            logging.error(f"Login bypass failed: {e}")
            self.driver.quit()
            sys.exit(1)

    # --- Utilities ---

    def _parse_size_bytes(self, size_str):
        """Parses TiB/GiB/TB/GB to Bytes."""
        size_str = size_str.strip().lower()
        try:
            size_str = size_str.replace("&nbsp;", " ").replace("  ", " ")
            parts = size_str.split()
            if not parts: return 0.0
            
            val = float(parts[0])
            unit = parts[1] if len(parts) > 1 else ""

            if "tib" in unit: return val * (1024**4)
            if "gib" in unit: return val * (1024**3)
            if "tb" in unit: return val * (1000**4) 
            if "gb" in unit: return val * (1000**3)
            return val
        except:
            return 0.0

    def _parse_seeding_days(self, time_str):
        """Parses '29 days, 5 hrs...' to integer days."""
        try:
            if "days" not in time_str and "day" not in time_str:
                return 0
            match = re.search(r'(\d+)\s*day', time_str)
            if match:
                return int(match.group(1))
            return 0
        except:
            return 0

    # --- History (Permanent ID Storage) ---
    
    def _load_history(self):
        """Load all previously downloaded torrent IDs."""
        if not os.path.exists(HISTORY_FILE): return set()
        with open(HISTORY_FILE, 'r') as f:
            return set(line.strip() for line in f if line.strip())

    def _add_history(self, torrent_id):
        """Add a torrent ID to permanent history."""
        # Ensure file ends with newline before appending
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'rb') as f:
                f.seek(-1, 2)  # Go to last byte
                if f.read(1) != b'\n':
                    with open(HISTORY_FILE, 'a') as fa:
                        fa.write('\n')
        
        with open(HISTORY_FILE, 'a') as f:
            f.write(f"{torrent_id}\n")
        logging.info(f"Added ID {torrent_id} to history.")

    # --- Active Torrent JSON (TL Name + qBit Name) ---
    
    def _load_active(self):
        """Load the current active torrent's names."""
        if not os.path.exists(ACTIVE_FILE):
            return None
        try:
            with open(ACTIVE_FILE, 'r') as f:
                return json.load(f)
        except:
            return None

    def _save_active(self, tl_name, qbit_name):
        """Save active torrent info to JSON."""
        data = {
            "tl_name": tl_name,
            "qbit_name": qbit_name,
            "added_at": datetime.now().isoformat()
        }
        with open(ACTIVE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logging.info(f"Saved active torrent: TL='{tl_name}' | qBit='{qbit_name}'")
        
    def _clear_active(self):
        """Clear the active torrent file."""
        if os.path.exists(ACTIVE_FILE):
            os.remove(ACTIVE_FILE)
            logging.info("Cleared active torrent file.")

    # --- Phase Methods ---

    def check_active_download(self):
        """Returns the first torrent in target category, if any."""
        torrents = self.qbt_client.torrents_info(category=TARGET_CATEGORY)
        return torrents[0] if torrents else None

    def monitor_phase(self, qbit_torrent):
        """
        Category is occupied. Check TL seeding page for our torrent's seeding time.
        """
        logging.info("--- Monitor Phase ---")
        
        # Load the saved TL name
        active = self._load_active()
        if not active:
            logging.warning("No active.json found. Cannot match on TL.")
            return
            
        tl_name = active.get("tl_name")
        logging.info(f"Looking for TL name: '{tl_name}'")
        
        self.driver.get(self.seeding_url)
        if not self._handle_cloudflare(): return
        
        try:
            WebDriverWait(self.driver, 30).until(EC.presence_of_element_located(SEEDING_TABLE_SELECTOR))
            time.sleep(2)
        except TimeoutException:
            logging.warning("Timed out waiting for seeding table.")
            return

        rows = self.driver.find_elements(*SEEDING_ROWS_SELECTOR)
        found = False
        
        for row in rows:
            try:
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 10: continue

                # Get the torrent name (exclude span tags like FL, 2160p, etc.)
                name_el = cols[0].find_element(By.CSS_SELECTOR, "a.torrent_name")
                row_name = self.driver.execute_script(
                    "return arguments[0].childNodes[0].textContent;", 
                    name_el
                ).strip()
                
                logging.info(f"Found row: '{row_name}'")
                
                # Match by TL name
                if row_name == tl_name:
                    found = True
                    
                    # Seeding time is in column 9 (index 9)
                    time_text = cols[9].text
                    days = self._parse_seeding_days(time_text)
                    
                    logging.info(f"Match found: '{row_name}'")
                    logging.info(f"Seeding time: {time_text} ({days} days)")
                    
                    if days >= TARGET_DAYS:
                        logging.info(f"SUCCESS! Seeded for {days} days (>= {TARGET_DAYS}). REMOVING.")
                        self.qbt_client.torrents_delete(torrent_hashes=[qbit_torrent.hash], delete_files=True)
                        logging.info("Torrent and files removed from qBittorrent.")
                        self._clear_active()
                    else:
                        logging.info(f"Keep seeding. ({days}/{TARGET_DAYS} days)")
                    
                    break
                    
            except Exception as e:
                logging.error(f"Error parsing seeding row: {e}")
                continue

        if not found:
            logging.warning(f"Torrent '{tl_name}' not found in search results.")
            logging.info("This could mean: still registering as seeder, or name mismatch.")

    def download_phase(self):
        """
        Category is empty. Find the smallest torrent >= 1TiB with enough seeders.
        """
        logging.info("--- Download Phase ---")
        
        history = self._load_history()
        page = 1
        
        all_candidates = []
        reached_threshold = False

        while page <= 50 and not reached_threshold:
            url = f"{BROWSE_URL}/page/{page}"
            logging.info(f"Scanning Page {page}...")
            self.driver.get(url)
            if not self._handle_cloudflare(): break
            
            try:
                WebDriverWait(self.driver, 20).until(EC.presence_of_element_located(TORRENT_TABLE_SELECTOR))
                time.sleep(2)
                
                rows = self.driver.find_elements(*TABLE_ROWS_SELECTOR)
                if not rows:
                    logging.info("No more rows found.")
                    break

                for row in rows:
                    try:
                        # Get size
                        size_el = row.find_element(By.CLASS_NAME, "td-size")
                        size_bytes = self._parse_size_bytes(size_el.text)
                        
                        # If we hit < 1TiB, we've found the threshold
                        if size_bytes < MIN_SIZE_BYTES:
                            logging.info(f"Reached torrents below 1 TiB on page {page}. Threshold found.")
                            reached_threshold = True
                            break  # Stop scanning this page

                        # Get torrent ID
                        tid = row.get_attribute("data-tid")
                        
                        # Check seeders
                        seeders_el = row.find_element(By.CLASS_NAME, "td-seeders")
                        seeders = int(seeders_el.text)
                        
                        if seeders < MIN_SEEDERS:
                            logging.debug(f"Skipping ID {tid} (only {seeders} seeders)")
                            continue

                        # Get the TL name
                        name_cell = row.find_element(By.CLASS_NAME, "td-name")
                        name_link = name_cell.find_element(By.CSS_SELECTOR, "a[href*='/torrent/']")
                        tl_name = self.driver.execute_script(
                            "return arguments[0].childNodes[0].textContent;", 
                            name_link
                        ).strip()
                        
                        # Get download link
                        download_link = row.find_element(By.CSS_SELECTOR, "a.download").get_attribute("href")
                        
                        size_tib = size_bytes / (1024**4)
                        
                        # Add to candidates list
                        all_candidates.append({
                            'tid': tid,
                            'tl_name': tl_name,
                            'size_bytes': size_bytes,
                            'size_tib': size_tib,
                            'seeders': seeders,
                            'download_link': download_link,
                            'in_history': tid in history
                        })
                        
                    except Exception as e:
                        logging.error(f"Error parsing row: {e}")
                        continue
                
                page += 1
                
            except TimeoutException:
                logging.warning("Timed out waiting for table.")
                break

        if not all_candidates:
            logging.info("No candidates found >= 1 TiB with enough seeders.")
            return

        # Sort all candidates by size ASCENDING (smallest first)
        all_candidates.sort(key=lambda x: x['size_bytes'])
        
        logging.info(f"Found {len(all_candidates)} total candidates >= 1 TiB")
        logging.info(f"Smallest: {all_candidates[0]['size_tib']:.2f} TiB | Largest: {all_candidates[-1]['size_tib']:.2f} TiB")
        
        # Find the smallest one NOT in history
        for candidate in all_candidates:
            if candidate['in_history']:
                logging.debug(f"Skipping {candidate['tid']} ({candidate['size_tib']:.2f} TiB) - already in history")
                continue
            
            # Found our target!
            logging.info(f"Selected candidate: '{candidate['tl_name']}'")
            logging.info(f"Size: {candidate['size_tib']:.2f} TiB | Seeders: {candidate['seeders']} | ID: {candidate['tid']}")
            
            self._download_and_add(candidate['tid'], candidate['tl_name'], candidate['download_link'])
            return
        
        # If we get here, ALL candidates are in history
        logging.info("All candidates >= 1 TiB are already in history. Nothing new to download.")

    def _download_and_add(self, torrent_id, tl_name, download_url):
        """Download torrent and save both names to JSON."""
        try:
            logging.info("Downloading .torrent file...")
            
            resp = self.session.get(download_url, timeout=60)
            resp.raise_for_status()
            
            # Validate that we actually got a torrent file
            if not resp.content.startswith(b'd'):
                logging.error("Downloaded content is NOT a valid torrent file!")
                if b'<html' in resp.content.lower() or b'<!doctype' in resp.content.lower():
                    logging.error("Got HTML instead of torrent - session cookies may be invalid!")
                return
            
            logging.info(f"Torrent file valid ({len(resp.content)} bytes)")
            
            existing_hashes = {t.hash for t in self.qbt_client.torrents_info()}
            
            # Add to qBit
            logging.info("Sending to qBittorrent...")
            res = self.qbt_client.torrents_add(
                torrent_files=resp.content,
                is_paused=False
            )
            
            if res != "Ok.":
                logging.error(f"qBittorrent rejected torrent: '{res}'")
                return
            
            logging.info("Torrent accepted. Waiting for it to appear...")
            time.sleep(3)
            
            all_torrents = self.qbt_client.torrents_info()
            new_torrents = [t for t in all_torrents if t.hash not in existing_hashes]
            
            if not new_torrents:
                logging.error("No NEW torrent found after adding! (Possibly a duplicate)")
                return
            
            new_torrent = new_torrents[0]
            qbit_name = new_torrent.name
            logging.info(f"Found NEW torrent: '{qbit_name}'")
            
            # Now set the category
            self.qbt_client.torrents_set_category(torrent_hashes=[new_torrent.hash], category=TARGET_CATEGORY)
            logging.info(f"Set category to '{TARGET_CATEGORY}'")
            
            # Save to history and active
            self._add_history(torrent_id)
            self._save_active(tl_name, qbit_name)
            logging.info("SUCCESS!")
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error downloading torrent: {e}")
        except Exception as e:
            logging.error(f"Error during download/add: {e}", exc_info=True)

    def run(self):
        try:
            self._init_driver()
            self._init_qbit()
            self._login_tl()
            
            # Check qBit for active torrent
            active_torrent = self.check_active_download()
            
            if active_torrent:
                logging.info(f"Category '{TARGET_CATEGORY}' is occupied.")
                self.monitor_phase(active_torrent)
            else:
                logging.info(f"Category '{TARGET_CATEGORY}' is empty. Searching for new torrent...")
                self.download_phase()
                
        except KeyboardInterrupt:
            logging.info("Stopping...")
        finally:
            if self.driver:
                self.driver.quit()
            logging.info("Done.")

if __name__ == "__main__":
    hunter = BigBoyHunter()
    hunter.run()
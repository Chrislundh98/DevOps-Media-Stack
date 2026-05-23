import logging
import os
import sys
import time
import re
import json
import shutil
import hashlib
import bencodepy
import requests
from pathlib import Path
from datetime import datetime, timedelta

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from qbittorrentapi import Client, APIConnectionError

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.base_monitor import BaseMonitor
from lib import CloudflareBypass, CookieManager, TorrentMatcher, create_chrome_driver

# Selectors
SHOW_TRANSFERS_BUTTON_SELECTOR = (By.XPATH, "//button[contains(@ng-click, 'togglePeers')]")
TRANSFERS_TABLE_SELECTOR = (By.XPATH, "//b[text()='Seeding:']/following-sibling::table[1]")
TRANSFERS_TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "tr[ng-repeat*='peer in vm.seeding']")
PAGINATION_NEXT_SELECTOR = (By.XPATH, "//ul[@ng-model='vm.currentPageSeeds']//li[contains(@class, 'pagination-next') and not(contains(@class, 'disabled'))]//a")
MYSEEDS_SEARCH_INPUT = (By.CSS_SELECTOR, "input[ng-model='vm.searchtorrents.name']")
MYSEEDS_SEEDING_DROPDOWN = (By.CSS_SELECTOR, "select[ng-model='vm.searchtorrents.seeding']")
MYSEEDS_TABLE = (By.CSS_SELECTOR, "table#myseeds")
MYSEEDS_TABLE_ROWS = (By.CSS_SELECTOR, "table#myseeds tbody tr[ng-repeat*='myseeds in vm.MyseedsGet']")
HNR_TABLE_SELECTOR = (By.CSS_SELECTOR, "table[ng-show='vm.snatchLoghnr']")
HNR_TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "tr[ng-repeat^='snatch in vm.snatchLoghnr']")
HNR_TORRENT_PAGE_LINK_SELECTOR = (By.CSS_SELECTOR, "a[ui-sref^='torrent(']")
HNR_TORRENT_DOWNLOAD_LINK_SELECTOR = (By.XPATH, "//a[.//button[contains(text(), 'Download torrent')]]")
HNR_PAGINATION_NEXT_DISABLED_SELECTOR = (By.XPATH, "//ul[contains(@class, 'pagination')]//li[contains(@class, 'pagination-next') and contains(@class, 'disabled')]")
HNR_PAGINATION_NEXT_SELECTOR = (By.XPATH, "//ul[contains(@class, 'pagination')]//li[contains(@class, 'pagination-next') and not(contains(@class, 'disabled'))]//a")

class DigitalCoreMonitor(BaseMonitor):

    DC_TRACKER_PATTERNS = ['trackerprxy.digitalcore.club', 'tracker.digitalcore.club']
    TL_TRACKER_PATTERNS = ['tracker.tleechreload.org', 'tracker.torrentleech.org']

    HNR_DOWNLOAD_PATH = "/mnt/storage/data/downloads/watch"
    HNR_CATEGORY = "manual"
    HNR_TAG = "DC"

    def __init__(self):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'digitalcore' / 'monitor'
        storage_dir = base_dir / 'storage' / 'json'

        super().__init__('DigitalCore', log_dir, storage_dir)

        self.qbit_url = os.getenv('QBIT_URL')
        self.qbit_user = os.getenv('QBIT_USER')
        self.qbit_pass = os.getenv('QBIT_PASS')

        self.cookie_file = storage_dir / 'dc_cookie.json'
        self.chrome_profile = base_dir / 'storage' / 'chrome_profiles' / 'dc_monitor'
        self.debug_screenshot = log_dir / 'dc_debug_failure.png'

        self.chrome_profile.mkdir(parents=True, exist_ok=True)

        self.base_url = "https://digitalcore.club"
        self.user_id = os.getenv('DC_USER_ID')
        self.user_handle = os.getenv('DC_USER_HANDLE')
        self.user_profile_url = f"{self.base_url}/user/{self.user_id}/{self.user_handle}"
        self.hnr_url = "https://digitalcore.club/hnr"
        self.myseeds_url = "https://digitalcore.club/myseeds"

        self.session = requests.Session()

    def _init_driver(self):
        logging.info("Starting Chrome browser (undetected_chromedriver)...")
        self.driver = create_chrome_driver(
            profile_dir=self.chrome_profile,
            page_load_timeout=120,
        )
        logging.info("Chrome driver created successfully")

    def _login(self):
        logging.info(f"Loading cookies from {self.cookie_file}...")
        if not self.cookie_file.exists():
            logging.error(f"Cookie file not found: {self.cookie_file}")
            return False

        try:
            with open(self.cookie_file, 'r') as f:
                cookies = json.load(f)
            if not cookies:
                logging.error("Cookie file is empty.")
                return False
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode cookie JSON: {e}")
            return False

        self.driver.get(self.base_url)
        time.sleep(2)

        added = 0
        for cookie in cookies:
            if "name" not in cookie or "value" not in cookie or cookie["name"] == "":
                continue
            if 'expirationDate' in cookie:
                cookie['expires'] = int(cookie.pop('expirationDate'))

            c_dict = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie.get('domain', '.digitalcore.club'),
                'path': cookie.get('path', '/'),
                'secure': cookie.get('secure', False),
            }
            try:
                self.driver.add_cookie(c_dict)
                added += 1
            except Exception as e:
                logging.debug(f"Failed to add cookie {cookie.get('name')}: {e}")

        logging.info(f"Added {added} cookies")

        self.driver.get(self.base_url)
        time.sleep(3)

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/user/']"))
            )
            logging.info("Successfully logged in to DigitalCore")
            return True
        except TimeoutException:
            logging.error("Login verification failed")
            try:
                self.driver.save_screenshot(str(self.debug_screenshot))
            except:
                pass
            return False

    def _transfer_cookies_to_session(self):
        for cookie in self.driver.get_cookies():
            self.session.cookies.set(cookie['name'], cookie['value'])

    # HNR FIXER

    def fix_hnr(self):
        logging.info("Checking for HnR warnings...")

        self.driver.get(self.hnr_url)
        time.sleep(3)

        try:
            table = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(HNR_TABLE_SELECTOR)
            )
        except TimeoutException:
            logging.info("No HnR table found. Assuming 0 HnRs.")
            return

        rows = self.driver.find_elements(*HNR_TABLE_ROWS_SELECTOR)
        if not rows:
            logging.info("No HnR warnings found")
            return

        logging.warning(f"Found {len(rows)} HnR warnings - downloading torrents")

        try:
            qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            qbt_client.auth_log_in()
        except Exception as e:
            logging.error(f"Failed to connect to qBittorrent: {e}")
            return

        self._transfer_cookies_to_session()
        added, failed, exists = 0, 0, 0

        for row in rows:
            try:
                torrent_link = row.find_element(*HNR_TORRENT_PAGE_LINK_SELECTOR)
                name = torrent_link.text.strip()
                torrent_url = torrent_link.get_attribute('href')

                logging.info(f"Processing HnR: {name}")

                self.driver.get(torrent_url)
                time.sleep(2)

                download_link = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located(HNR_TORRENT_DOWNLOAD_LINK_SELECTOR)
                )
                download_url = download_link.get_attribute('href')

                resp = self.session.get(download_url)
                resp.raise_for_status()

                try:
                    decoded = bencodepy.decode(resp.content)
                    info_hash = hashlib.sha1(bencodepy.encode(decoded[b'info'])).hexdigest()
                except:
                    logging.warning(f"Invalid torrent file for {name}")
                    failed += 1
                    continue

                existing = qbt_client.torrents_info(torrent_hashes=info_hash)
                if existing:
                    logging.info(f"Torrent already exists, skipping")
                    exists += 1
                    continue

                qbt_client.torrents_add(
                    torrent_files=resp.content,
                    category=self.HNR_CATEGORY,
                    tags=self.HNR_TAG
                )
                added += 1
                logging.info(f"Added: {name}")
                time.sleep(1)

            except Exception as e:
                logging.error(f"Failed to process HnR: {e}")
                failed += 1
                continue

        qbt_client.auth_log_out()

        logging.info(f"HnR Summary: {len(rows)} found, {added} added, {exists} existed, {failed} failed")

        if self.webhook_url and (added > 0 or failed > 0):
            embeds = [{
                "title": "🔧 DigitalCore HnR Fixer",
                "description": f"Found: **{len(rows)}**\nAdded: **{added}**\nExisted: **{exists}**\nFailed: **{failed}**",
                "color": 3066993 if failed == 0 else 15158332,
            }]
            self._send_discord(embeds)

    # SCRAPING

    def scrape_seeding_torrents(self):
        logging.info("Scraping seeding torrents from DigitalCore...")

        self.driver.get(self.user_profile_url)
        time.sleep(3)

        try:
            btn = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(SHOW_TRANSFERS_BUTTON_SELECTOR)
            )
            btn.click()
            time.sleep(2)
        except:
            logging.warning("Could not click show transfers button")

        torrents = []
        page = 1

        while True:
            try:
                table = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located(TRANSFERS_TABLE_SELECTOR)
                )
                rows = table.find_elements(*TRANSFERS_TABLE_ROWS_SELECTOR)

                for row in rows:
                    try:
                        cells = row.find_elements(By.TAG_NAME, 'td')
                        if len(cells) < 7:
                            continue

                        # DC column layout: 0=Type, 1=Torrent, 2=Port, 3=Size, 4=Seeders, 5=Leechers, 6=Uploaded
                        name_cell = cells[1]
                        name = ""

                        try:
                            name_link = name_cell.find_element(By.CSS_SELECTOR, "div.ellipsis a[title]")
                            name = name_link.get_attribute('title').strip()
                            if not name:
                                name = name_link.text.strip()
                        except:
                            try:
                                name_link = name_cell.find_element(By.TAG_NAME, "a")
                                name = name_link.get_attribute('title') or name_link.text.strip()
                            except:
                                name = name_cell.text.strip()

                        if not name:
                            continue

                        uploaded_str = cells[6].text.strip() if len(cells) >= 7 else "0"

                        link = name_cell.find_element(By.TAG_NAME, 'a')
                        torrent_url = link.get_attribute('href')
                        torrent_id = torrent_url.split('/')[-1] if torrent_url else None

                        torrents.append({
                            'id': torrent_id,
                            'name': name,
                            'uploaded_mb': self.parse_size_to_mb(uploaded_str),
                            'url': torrent_url
                        })
                    except Exception as e:
                        logging.debug(f"Failed to parse row: {e}")
                        continue

                logging.info(f"Page {page}: Scraped {len(torrents)} torrents total ({len(rows)} rows on page)")

                try:
                    next_btn = self.driver.find_element(*PAGINATION_NEXT_SELECTOR)
                    next_btn.click()
                    time.sleep(2)
                    page += 1
                except:
                    logging.info("No more pages")
                    break

            except Exception as e:
                logging.error(f"Error scraping page {page}: {e}")
                break

        # Check seed time via /myseeds (batch - navigate once)
        if torrents:
            logging.info(f"Checking seed time status for {len(torrents)} torrents on /myseeds...")
            try:
                self.driver.get(self.myseeds_url)
                time.sleep(3)

                # Set Seeding filter to Yes (value "0" = Yes in DC's dropdown)
                try:
                    seeding_dropdown = Select(WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(MYSEEDS_SEEDING_DROPDOWN)
                    ))
                    seeding_dropdown.select_by_value("0")
                    time.sleep(2)
                except Exception as e:
                    logging.error(f"Could not set seeding filter: {e}")

                search_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located(MYSEEDS_SEARCH_INPUT)
                )

                done_count = 0
                pending_count = 0
                not_found = 0

                for i, torrent in enumerate(torrents):
                    try:
                        search_input.clear()
                        time.sleep(0.3)
                        search_input.send_keys(torrent['name'][:50])
                        time.sleep(2)  # Wait for Angular debounce + update

                        rows = self.driver.find_elements(*MYSEEDS_TABLE_ROWS)
                        torrent['seed_time_done'] = False

                        found = False
                        for row in rows:
                            cells = row.find_elements(By.XPATH, "./td")
                            if len(cells) >= 10:
                                try:
                                    name_link = cells[0].find_element(By.CSS_SELECTOR, "a[title]")
                                    row_name = name_link.get_attribute('title').strip()
                                except:
                                    row_name = cells[0].text.strip().split('\n')[0]

                                if row_name == torrent['name']:
                                    seed_time_left = cells[9].text.strip().lower()
                                    if 'done' in seed_time_left:
                                        torrent['seed_time_done'] = True
                                        done_count += 1
                                    else:
                                        pending_count += 1
                                    found = True
                                    break

                        if not found:
                            not_found += 1

                    except Exception as e:
                        logging.debug(f"Could not check seed time for {torrent['name'][:30]}: {e}")
                        torrent['seed_time_done'] = False

                logging.info(f"Seed time check: {done_count} done, {pending_count} pending, {not_found} not found")

            except Exception as e:
                logging.error(f"Failed to load /myseeds page: {e}")
                for t in torrents:
                    t.setdefault('seed_time_done', False)

        logging.info(f"Total torrents scraped: {len(torrents)}")
        return torrents

    # REMOVAL RULES

    def apply_removal_rules(self, tracker_torrents):
        try:
            qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            qbt_client.auth_log_in()
            all_torrents = qbt_client.torrents_info()
        except Exception as e:
            logging.error(f"Failed to connect to qBittorrent: {e}")
            return []

        # Filter DC-only torrents (exclude cross-seeded with TL)
        dc_qbit_torrents = []
        for torrent in all_torrents:
            is_dc = False
            is_tl = False
            for tracker in torrent.trackers:
                url = tracker.get('url', '').lower()
                if any(p in url for p in self.DC_TRACKER_PATTERNS):
                    is_dc = True
                if any(p in url for p in self.TL_TRACKER_PATTERNS):
                    is_tl = True
            if is_dc and not is_tl:
                dc_qbit_torrents.append(torrent)

        logging.info(f"Found {len(dc_qbit_torrents)} DC-only torrents in qBittorrent")

        to_remove = []
        failed_matches = []

        for dc_torrent in tracker_torrents:
            if not dc_torrent.get('seed_time_done'):
                continue

            qbit_match, score, method = self.matcher.find_best_match(
                dc_torrent['name'], dc_qbit_torrents,
                tracker_tag='DC', tracker_size_mb=None
            )

            if not qbit_match:
                continue

            qbit_size_mb = qbit_match.size / (1024 * 1024)
            days_seeded = (time.time() - qbit_match.added_on) / 86400
            uploaded_mb = dc_torrent['uploaded_mb']
            ratio = uploaded_mb / qbit_size_mb if qbit_size_mb > 0 else 0

            remove = False
            reason = None

            if days_seeded >= 7 and uploaded_mb < 50:
                remove = True
                reason = f"7+ days, <50MB uploaded"
            elif days_seeded >= 10 and ratio < 0.02:
                remove = True
                reason = f"10+ days, <2% ratio"
            elif days_seeded >= 14 and uploaded_mb < 200:
                remove = True
                reason = f"14+ days, <200MB uploaded"
            elif days_seeded >= 18 and uploaded_mb < 10240:
                remove = True
                reason = f"18+ days, <10GB uploaded"
            elif days_seeded >= 20 and ratio < 0.01:
                remove = True
                reason = f"20+ days, <1% ratio"

            if remove:
                to_remove.append({
                    'name': dc_torrent['name'],
                    'hash': qbit_match.hash,
                    'size_mb': qbit_size_mb,
                    'days_seeding': days_seeded,
                    'reason': reason
                })

        qbt_client.auth_log_out()
        return to_remove

    # MAIN RUN

    def run(self):
        logging.info("=" * 80)
        logging.info("STARTING DIGITALCORE MONITOR")
        logging.info("=" * 80)

        try:
            self._init_driver()
        except Exception as e:
            logging.error(f"Failed to initialize Chrome driver: {e}")
            self._send_error_notification(str(e), context="DigitalCore Monitor")
            return

        try:
            if not self._login():
                return

            # 1. Fix HnRs
            self.fix_hnr()

            # 2. Scrape seeding torrents
            tracker_torrents = self.scrape_seeding_torrents()
            if not tracker_torrents:
                logging.warning("No torrents found on tracker")
                return

            logging.info(f"Found {len(tracker_torrents)} torrents on tracker")

            # 3. Apply removal rules
            to_remove = self.apply_removal_rules(tracker_torrents)
            if not to_remove:
                logging.info("No torrents met removal criteria")
                return

            # 4. Delete
            logging.info(f"Removing {len(to_remove)} torrents from qBittorrent")
            try:
                qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
                qbt_client.auth_log_in()
                hashes = [t['hash'] for t in to_remove]
                qbt_client.torrents_delete(torrent_hashes=hashes, delete_files=True)
                qbt_client.auth_log_out()
            except Exception as e:
                logging.error(f"Failed to delete torrents: {e}")

            # 5. Notify
            self._send_removal_notification(to_remove)
            self.matcher.save_match_data()

        except Exception as e:
            logging.error(f"Error in monitor: {e}", exc_info=True)
            self._send_error_notification(str(e), context="DigitalCore Monitor")
        finally:
            if self.driver:
                self.driver.quit()
            logging.info("=" * 80)
            logging.info("DIGITALCORE MONITOR - Completed")
            logging.info("=" * 80)

    def _send_removal_notification(self, removed_list):
        if not self.webhook_url or not removed_list:
            return

        desc = "\n".join([
            f"• {t['name']} **({self.format_size(t['size_mb'])})**"
            for t in removed_list
        ])
        if len(desc) > 4096:
            desc = desc[:4000] + "\n\n... (truncated)"

        total_size = sum(t['size_mb'] for t in removed_list)
        embeds = [{
            "title": f"✅ DC: Removed {len(removed_list)} Stalled Torrents",
            "description": desc,
            "color": 3066993,
            "fields": [{"name": "Total Storage Saved", "value": self.format_size(total_size), "inline": False}]
        }]

        self._send_discord(embeds, username="DigitalCore Monitor")

if __name__ == "__main__":
    monitor = DigitalCoreMonitor()
    monitor.run()
import logging
import os
import sys
import time
import re
import json
import random
import requests
import bencodepy
from pathlib import Path
from datetime import datetime, timedelta

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from qbittorrentapi import Client, APIConnectionError

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.base_monitor import BaseMonitor
from lib import CloudflareBypass, CookieManager, TorrentMatcher, create_chrome_driver
from lib.torrent_lifecycle import TorrentLifecycleTracker

# Selectors
LOGGED_IN_PROOF_SELECTOR = (By.CSS_SELECTOR, "a[href*='/user/profile']")
SEEDING_TABLE_SELECTOR = (By.ID, "profile-seedingTable")
TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "table#profile-seedingTable tbody tr")
NEXT_BUTTON_SELECTOR = (By.CSS_SELECTOR, "li.next a")
NEXT_BUTTON_DISABLED_SELECTOR = (By.CSS_SELECTOR, "li.next.disabled")
NOTIFICATION_COUNT_SELECTOR = (By.CSS_SELECTOR, "span.tooltip-title.link")
NOTIFICATION_TABLE_SELECTOR = (By.ID, "notificationsTable")
NOTIFICATION_TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "table#notificationsTable tbody tr")
HNR_TABLE_SELECTOR = (By.ID, "harsTable")
HNR_TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "table#harsTable tbody tr")
TORRENT_DOWNLOAD_LINK_SELECTOR = (By.ID, "detailsDownloadButton")
HNR_NEXT_BUTTON_SELECTOR = (By.CSS_SELECTOR, "li.paginate_button.next:not(.disabled) a")
HNR_NEXT_BUTTON_DISABLED_SELECTOR = (By.CSS_SELECTOR, "li.paginate_button.next.disabled")

class TorrentLeechMonitor(BaseMonitor):

    TL_TRACKER_PATTERNS = ['tracker.tleechreload.org', 'tracker.torrentleech.org']
    DC_TRACKER_PATTERNS = ['trackerprxy.digitalcore.club', 'tracker.digitalcore.club']

    HNR_DOWNLOAD_PATH = "/mnt/storage/data/downloads/watch"
    HNR_CATEGORY = "manual"
    HNR_TAG = "TL"
    REMOVAL_CACHE_RETENTION_DAYS = 7

    def __init__(self):
        base_dir = Path(__file__).parent.parent
        log_dir = base_dir / 'logs' / 'torrentleech' / 'monitor'
        storage_dir = base_dir / 'storage' / 'json'

        super().__init__('TorrentLeech', log_dir, storage_dir)

        self.username = os.getenv('USERNAME')
        self.password = os.getenv('PASSWORD')
        self.qbit_url = os.getenv('QBIT_URL')
        self.qbit_user = os.getenv('QBIT_USER')
        self.qbit_pass = os.getenv('QBIT_PASS')

        self.cookie_file = self.storage_dir / 'tl_cookie.json'
        self.longterm_cache_file = self.storage_dir_txt / 'longterm_torrents.txt'
        self.history_file = self.storage_dir / 'seeding_growth_history.json'
        self.removal_cache_file = self.storage_dir / 'recent_removals_cache.json'
        self.daily_saved_cache = self.storage_dir / 'daily_saved_storage.json'
        self.debug_screenshot = self.log_dir / 'debug_failure.png'

        self.seeding_url = f"https://www.torrentleech.org/profile/{self.username}/seeding"
        self.hnr_url = f"https://www.torrentleech.org/profile/{self.username}/hnr"
        self.notifications_url = f"https://www.torrentleech.org/profile/{self.username}/notifications"

        self.session = requests.Session()

    # CHROME DRIVER & AUTH

    def _init_driver(self, cookies, user_agent):
        self.driver = create_chrome_driver(user_agent=user_agent)
        logging.info("Chrome driver initialized (undetected_chromedriver).")

    def _login(self, cookies):
        try:
            self.driver.get("https://www.torrentleech.org/robots.txt")
        except:
            pass

        for c in cookies:
            c_dict = {
                'name': c['name'],
                'value': c['value'],
                'domain': c.get('domain', '.torrentleech.org'),
                'path': '/',
                'secure': c.get('secure', True)
            }
            if not c_dict['domain'].startswith('.'):
                c_dict['domain'] = '.' + c_dict['domain']
            try:
                self.driver.add_cookie(c_dict)
            except Exception as e:
                logging.debug(f"Failed to add cookie {c['name']}: {e}")

        logging.info("Cookies injected. Checking access...")

        self.driver.get("https://www.torrentleech.org/")
        time.sleep(3)

        if not CloudflareBypass.handle_challenge(self.driver):
            logging.error("Failed to bypass Cloudflare. Exiting.")
            return False

        if "login" in self.driver.current_url.lower():
            logging.error("Cookie authentication failed - redirected to login page.")
            self._take_screenshot()
            return False

        logging.info("Cookie authentication successful!")
        return True

    def _take_screenshot(self):
        try:
            self.driver.save_screenshot(str(self.debug_screenshot))
            logging.info(f"Screenshot saved to {self.debug_screenshot}")
        except:
            pass

    def _transfer_cookies_to_session(self):
        for cookie in self.driver.get_cookies():
            self.session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', ''))

    # HNR FIXER

    def fix_hnr(self):
        logging.info("Checking for HnR warnings...")

        self.driver.get(self.hnr_url)
        if not CloudflareBypass.handle_challenge(self.driver):
            return

        try:
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located(HNR_TABLE_SELECTOR))
        except TimeoutException:
            logging.info("No HnR table found. Assuming 0 HnRs.")
            return

        all_urls = []
        page = 1
        while True:
            logging.info(f"--- HnR Scraping Page {page} ---")
            rows = self.driver.find_elements(*HNR_TABLE_ROWS_SELECTOR)
            for row in rows:
                try:
                    td = row.find_element(By.TAG_NAME, "td")
                    click = td.get_attribute("onclick")
                    m = re.search(r"window\.location='(.*?)'", click)
                    if m:
                        all_urls.append("https://www.torrentleech.org" + m.group(1))
                except:
                    continue

            try:
                self.driver.find_element(*HNR_NEXT_BUTTON_DISABLED_SELECTOR)
                break
            except NoSuchElementException:
                try:
                    btn = self.driver.find_element(*HNR_NEXT_BUTTON_SELECTOR)
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    if not CloudflareBypass.handle_challenge(self.driver):
                        break
                    page += 1
                except:
                    break

        if not all_urls:
            logging.info("No HnR torrents found.")
            return

        logging.info(f"Found {len(all_urls)} HnRs to process.")
        recent = self._load_recent_removals()

        try:
            qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            qbt_client.auth_log_in()
            # Ensure HnR category exists
            cats = qbt_client.torrents_categories()
            if self.HNR_CATEGORY not in cats:
                qbt_client.torrents_create_category(
                    category=self.HNR_CATEGORY,
                    save_path=self.HNR_DOWNLOAD_PATH
                )
                logging.info(f"Created qBittorrent category '{self.HNR_CATEGORY}'")
        except Exception as e:
            logging.error(f"Failed to connect to qBittorrent: {e}")
            return

        added, failed, skipped, exists = 0, 0, 0, 0

        for url in all_urls:
            self.driver.get(url)
            if not CloudflareBypass.handle_challenge(self.driver):
                skipped += 1
                continue

            try:
                dl_btn = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located(TORRENT_DOWNLOAD_LINK_SELECTOR)
                )
                dl_url = dl_btn.get_attribute('href')
                t_name = dl_url.split('/')[-1]

                fp_match = self._check_for_false_positive(t_name, recent)
                if fp_match:
                    logging.info(f"Note: {t_name} was previously removed by monitor (matched: {fp_match.get('tl_name', 'unknown')}). Re-downloading to fix HnR.")

                self._transfer_cookies_to_session()
                logging.info(f"Downloading torrent: {dl_url}")
                resp = self.session.get(dl_url)
                resp.raise_for_status()
                logging.info(f"Downloaded {len(resp.content)} bytes for {t_name}")

                if not self._validate_torrent_content(resp.content):
                    logging.error(f"Invalid torrent content for {t_name} (got {len(resp.content)} bytes, starts with: {resp.content[:50]})")
                    failed += 1
                    continue

                thash = self._get_torrent_hash(resp.content)
                if thash and self._torrent_exists_in_qbit(qbt_client, thash):
                    exists += 1
                    continue

                qbt_client.torrents_add(
                    torrent_files=resp.content,
                    category=self.HNR_CATEGORY,
                    tags=self.HNR_TAG
                )
                added += 1
                logging.info(f"Added: {t_name}")
                time.sleep(1)

            except Exception as e:
                logging.error(f"Error processing {url}: {e}")
                failed += 1

        qbt_client.auth_log_out()

        self._send_hnr_notification(len(all_urls), added, failed, skipped, exists)
        logging.info("HnR check complete.")

    def _load_recent_removals(self):
        if not self.removal_cache_file.exists():
            return []
        try:
            with open(self.removal_cache_file, 'r') as f:
                cache_data = json.load(f)
            return cache_data.get('removals', [])
        except:
            return []

    def _check_for_false_positive(self, hnr_torrent_name, recent_removals):
        if not recent_removals:
            return None
        hnr_clean = hnr_torrent_name.lower().replace('.torrent', '').replace('_', ' ').replace('.', ' ')
        for removal in recent_removals:
            tl_name = removal.get('tl_name', '')
            if tl_name:
                tl_clean = tl_name.lower().replace('.torrent', '').replace('_', ' ').replace('.', ' ')
                if hnr_clean == tl_clean or hnr_clean in tl_clean or tl_clean in hnr_clean:
                    return removal
            qbit_name = removal.get('qbit_name', '')
            if qbit_name:
                qbit_clean = qbit_name.lower().replace('.torrent', '').replace('_', ' ').replace('.', ' ')
                if hnr_clean == qbit_clean or hnr_clean in qbit_clean or qbit_clean in hnr_clean:
                    return removal
        return None

    def _validate_torrent_content(self, content):
        try:
            bencodepy.decode(content)
            return True
        except:
            logging.warning("Downloaded content is not a valid torrent file")
            return False

    def _get_torrent_hash(self, content):
        import hashlib
        try:
            decoded = bencodepy.decode(content)
            info_encoded = bencodepy.encode(decoded[b'info'])
            return hashlib.sha1(info_encoded).hexdigest()
        except:
            return None

    def _torrent_exists_in_qbit(self, qbt_client, torrent_hash):
        try:
            existing = qbt_client.torrents_info(torrent_hashes=torrent_hash)
            if existing:
                logging.info(f"Torrent already exists in qBittorrent (hash: {torrent_hash[:8]}...)")
                return True
        except:
            pass
        return False

    def _send_hnr_notification(self, total, added, failed, skipped, exists):
        if not self.webhook_url:
            return

        lines = [
            f"Total HnRs found: **{total}**",
            f"Added to qBittorrent: **{added}**",
            f"Already existed: **{exists}**",
            f"Failed to download: **{failed}**",
            f"Skipped (Cloudflare): **{skipped}**"
        ]

        embeds = [{
            "title": "🔧 TorrentLeech HnR Fixer",
            "description": "\n".join(lines),
            "color": 3066993 if failed == 0 else 15158332,
        }]

        self._send_discord(embeds)

    # ADMIN DELETION NOTIFICATIONS

    def _get_notification_count(self):
        try:
            count_element = self.driver.find_element(*NOTIFICATION_COUNT_SELECTOR)
            count_text = count_element.text.strip()
            if count_text.isdigit():
                count = int(count_text)
                logging.info(f"Notification count from navbar: {count}")
                return count
            return 0
        except NoSuchElementException:
            return 0
        except Exception as e:
            logging.warning(f"Error reading notification count: {e}")
            return 0

    def _check_and_process_admin_deletions(self):
        removed = []
        failed = []

        logging.info("\n" + "=" * 80)
        logging.info("CHECKING FOR ADMIN DELETION NOTIFICATIONS")
        logging.info("=" * 80)

        notification_count = self._get_notification_count()
        if notification_count == 0:
            logging.info("No unread notifications. Skipping.")
            return removed, failed

        logging.info(f"Found {notification_count} unread notification(s). Checking for deletions...")

        self.driver.get(self.notifications_url)
        if not CloudflareBypass.handle_challenge(self.driver):
            return removed, failed

        try:
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located(NOTIFICATION_TABLE_SELECTOR))
            time.sleep(2)
        except TimeoutException:
            logging.error("Timeout waiting for notifications table")
            return removed, failed

        deletion_notifications = []
        try:
            rows = self.driver.find_elements(*NOTIFICATION_TABLE_ROWS_SELECTOR)
            logging.info(f"Found {len(rows)} notification rows, checking first {notification_count} (unread)")

            for i, row in enumerate(rows[:notification_count]):
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 2:
                        continue

                    message = ""
                    for cell in cells:
                        cell_text = cell.text.strip()
                        if cell_text.startswith("Torrent "):
                            message = cell_text
                            break

                    if not message:
                        message = row.text.strip()

                    if not message.startswith("Torrent "):
                        continue
                    if "has been deleted" not in message.lower():
                        continue
                    if "reseed" in message.lower():
                        continue

                    match = re.match(r'^Torrent (.+?) has been deleted\.?\s*Reason:?\s*(.*)$', message, re.IGNORECASE)
                    if match:
                        torrent_name = match.group(1).strip()
                        reason = match.group(2).strip() if match.group(2) else "Unknown"
                        deletion_notifications.append({
                            'name': torrent_name,
                            'reason': reason,
                            'row_index': i
                        })
                        logging.info(f"  Row {i+1}: Found deletion - '{torrent_name[:60]}...'")
                except Exception as e:
                    logging.debug(f"Error parsing row {i+1}: {e}")
                    continue

        except Exception as e:
            logging.error(f"Error reading notifications table: {e}")
            return removed, failed

        if not deletion_notifications:
            logging.info("No torrent deletions to process.")
            return removed, failed

        try:
            qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            qbt_client.auth_log_in()

            all_torrents = qbt_client.torrents_info()
            tl_torrents = self._get_tl_torrents_from_qbit(all_torrents)
            logging.info(f"Found {len(tl_torrents)} TL torrents in qBittorrent")

            hashes_to_delete = []

            for notification in deletion_notifications:
                torrent_name = notification['name']
                matched_torrent = None
                match_method = None

                for torrent in tl_torrents:
                    if torrent.name == torrent_name:
                        matched_torrent = torrent
                        match_method = "exact"
                        break

                if not matched_torrent:
                    for torrent in tl_torrents:
                        if torrent.name.lower() == torrent_name.lower():
                            matched_torrent = torrent
                            match_method = "exact-ci"
                            break

                if not matched_torrent:
                    for torrent in tl_torrents:
                        if torrent_name.lower() in torrent.name.lower() or torrent.name.lower() in torrent_name.lower():
                            matched_torrent = torrent
                            match_method = "substring"
                            break

                if not matched_torrent:
                    matched_torrent, _ = self.matcher.find_best_match(
                        torrent_name, tl_torrents, tracker_tag="TL", tracker_size_mb=None
                    )[:2]
                    if matched_torrent:
                        match_method = "fuzzy"

                if matched_torrent:
                    if self._is_protected_torrent(matched_torrent):
                        logging.warning(f"  SKIPPED - Protected torrent: '{matched_torrent.name}'")
                        failed.append(notification)
                        continue

                    qbit_size_mb = matched_torrent.size / (1024 * 1024)
                    hashes_to_delete.append(matched_torrent.hash)
                    notification['matched_name'] = matched_torrent.name
                    notification['match_method'] = match_method
                    notification['size_mb'] = qbit_size_mb
                    removed.append(notification)
                    logging.info(f"  MATCHED ({match_method}): '{matched_torrent.name}'")
                else:
                    notification['size_mb'] = 0.0
                    failed.append(notification)
                    logging.warning(f"  NO MATCH FOUND for: '{torrent_name}'")

            if hashes_to_delete:
                logging.info(f"DELETING {len(hashes_to_delete)} TORRENTS FROM QBITTORRENT")
                qbt_client.torrents_delete(torrent_hashes=hashes_to_delete, delete_files=True)
                logging.info("Successfully deleted torrents.")

            qbt_client.auth_log_out()

        except Exception as e:
            logging.error(f"Error during qBittorrent operations: {e}")
            import traceback
            logging.error(traceback.format_exc())

        if removed or failed:
            self._send_admin_removal_notification(removed, failed)

        return removed, failed

    def _send_admin_removal_notification(self, removed_list, failed_list):
        embeds = []
        MAX_DESC = 4096

        if removed_list:
            desc_lines = []
            for t in removed_list:
                line = f"• **{t['name'][:80]}** ({self.format_size(t.get('size_mb', 0))})"
                if t.get('reason'):
                    line += f"\n  └─ Reason: _{t['reason'][:100]}_"
                if t.get('match_method'):
                    line += f"\n  └─ Match: {t['match_method']}"
                desc_lines.append(line)
            desc = "\n".join(desc_lines)
            if len(desc) > MAX_DESC:
                desc = desc[:MAX_DESC - 50] + "\n\n... (truncated)"
            embeds.append({
                "title": f"🗑️ Admin Removed {len(removed_list)} Torrent(s) - Cleaned",
                "description": desc,
                "color": 7506394,
                "footer": {"text": "Torrents deleted by TL admins - no HnR penalty."}
            })

        if failed_list:
            desc_lines = [f"• **{t['name'][:80]}**" for t in failed_list]
            desc = "\n".join(desc_lines)
            if len(desc) > MAX_DESC:
                desc = desc[:MAX_DESC - 50] + "\n\n... (truncated)"
            embeds.append({
                "title": f"⚠️ Failed to Match {len(failed_list)} Admin-Removed Torrent(s)",
                "description": desc,
                "color": 15105570,
            })

        if embeds:
            self._send_discord(embeds, username="TorrentLeech Monitor")

    # LONGTERM CACHE & HISTORY

    def _load_longterm_ids(self):
        if not self.longterm_cache_file.exists():
            logging.warning(f"Longterm torrents cache not found at {self.longterm_cache_file}")
            return set()
        with open(self.longterm_cache_file, 'r') as f:
            ids = set(line.strip() for line in f if line.strip())
        logging.info(f"Loaded {len(ids)} longterm torrent IDs (will skip during scraping)")
        return ids

    def _load_history(self):
        if not self.history_file.exists():
            return {}
        try:
            with open(self.history_file, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save_history(self, history_data):
        try:
            with open(self.history_file, 'w') as f:
                json.dump(history_data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to save seeding history: {e}")

    def _update_history_snapshots(self, scraped_torrents, history_data):
        today = datetime.now().strftime("%Y-%m-%d")
        cutoff_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        updated_count = 0
        current_ids = set()

        for torrent in scraped_torrents:
            if torrent['days_seeding'] < 20:
                continue

            torrent_id = torrent['id']
            current_ids.add(torrent_id)
            uploaded_mb = torrent['uploaded_mb']

            if torrent_id not in history_data:
                history_data[torrent_id] = {
                    'name': torrent['name'],
                    'size_mb': torrent['size_mb'],
                    'snapshots': []
                }

            snapshots = history_data[torrent_id]['snapshots']

            if snapshots and snapshots[-1].get('date') == today:
                snapshots[-1]['uploaded_mb'] = uploaded_mb
            else:
                snapshots.append({'date': today, 'uploaded_mb': uploaded_mb})
                updated_count += 1

            history_data[torrent_id]['snapshots'] = [
                s for s in snapshots if s['date'] >= cutoff_date
            ]

        stale_ids = [tid for tid in list(history_data.keys()) if tid not in current_ids]
        for tid in stale_ids:
            del history_data[tid]

        if updated_count > 0:
            logging.info(f"History: {updated_count} snapshots added, {len(stale_ids)} stale entries removed")

        return history_data

    # REMOVAL CACHING

    def _log_removal_to_cache(self, tl_torrent, qbit_torrent, match_method, match_score):
        if self.removal_cache_file.exists():
            try:
                with open(self.removal_cache_file, 'r') as f:
                    cache_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                cache_data = {'removals': []}
        else:
            cache_data = {'removals': []}

        cache_data['removals'].append({
            'tl_name': tl_torrent['name'],
            'tl_id': tl_torrent.get('id', 'unknown'),
            'qbit_name': qbit_torrent.name,
            'qbit_hash': qbit_torrent.hash,
            'match_method': match_method,
            'match_score': round(match_score, 3),
            'tl_size_mb': round(tl_torrent.get('size_mb', 0), 2),
            'qbit_size_mb': round(qbit_torrent.size / (1024 * 1024), 2),
            'removed_at': datetime.now().isoformat(),
            'seeding_days': tl_torrent.get('seeding_days', 0),
            'removal_reason': tl_torrent.get('reason', 'unknown')
        })
        cache_data['last_updated'] = datetime.now().isoformat()

        cutoff = datetime.now() - timedelta(days=self.REMOVAL_CACHE_RETENTION_DAYS)
        cache_data['removals'] = [
            r for r in cache_data['removals']
            if datetime.fromisoformat(r['removed_at']) > cutoff
        ]

        try:
            with open(self.removal_cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except IOError as e:
            logging.error(f"Failed to write removal cache: {e}")

    def _cache_removed_storage(self, removed_list, failed_list=None):
        daily_log = {}
        if self.daily_saved_cache.exists():
            try:
                with open(self.daily_saved_cache, 'r') as f:
                    daily_log = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        new_entries = 0
        torrents_to_log = removed_list + (failed_list if failed_list else [])

        for torrent in torrents_to_log:
            name = torrent.get('name')
            size_mb = torrent.get('size_mb', 0.0)
            if not name or size_mb <= 0:
                continue
            unique_key = f"{name}::{size_mb:.2f}"
            if unique_key not in daily_log:
                daily_log[unique_key] = {
                    'name': name,
                    'size_mb': size_mb,
                    'added_at': datetime.now().isoformat()
                }
                new_entries += 1

        if new_entries > 0:
            try:
                with open(self.daily_saved_cache, 'w') as f:
                    json.dump(daily_log, f, indent=2)
                logging.info(f"Cached {new_entries} new torrent removals")
            except IOError as e:
                logging.error(f"Failed to write daily storage cache: {e}")

    # SCRAPING

    def scrape_seeding_torrents(self, longterm_ids=None):
        logging.info(f"Navigating to seeding page: {self.seeding_url}")
        self.driver.get(self.seeding_url)

        if not CloudflareBypass.handle_challenge(self.driver):
            logging.error("Failed to bypass Cloudflare on seeding page.")
            return []

        if longterm_ids is None:
            longterm_ids = set()

        scraped = []
        longterm_skipped = 0
        page = 1

        while True:
            logging.info(f"--- Scanning Page {page} ---")
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located(SEEDING_TABLE_SELECTOR))
            time.sleep(3)

            rows = self.driver.find_elements(*TABLE_ROWS_SELECTOR)

            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 11:
                    continue

                try:
                    url = cells[0].find_element(By.TAG_NAME, "a").get_attribute('href')
                    torrent_id = url.split('/torrent/')[1]
                except:
                    continue

                if torrent_id in longterm_ids:
                    longterm_skipped += 1
                    continue

                torrent_name = cells[0].text
                total_size_mb = self.parse_size_to_mb(cells[1].text)
                uploaded_mb = self.parse_size_to_mb(cells[4].text)
                seeding_time_str = cells[9].text
                days_seeding = self._parse_days(seeding_time_str)

                scraped.append({
                    'id': torrent_id,
                    'name': torrent_name,
                    'size_mb': total_size_mb,
                    'uploaded_mb': uploaded_mb,
                    'days_seeding': days_seeding
                })

            try:
                self.driver.find_element(*NEXT_BUTTON_DISABLED_SELECTOR)
                logging.info("Last page reached.")
                break
            except NoSuchElementException:
                self.driver.find_element(*NEXT_BUTTON_SELECTOR).click()
                page += 1

        logging.info(f"\n{'='*80}")
        logging.info(f"SCRAPING SUMMARY:")
        logging.info(f"  Pages scanned: {page}")
        logging.info(f"  Longterm torrents skipped: {longterm_skipped}")
        logging.info(f"  Torrents scraped: {len(scraped)}")
        logging.info(f"{'='*80}\n")

        return scraped

    # STALLED DETECTION & REMOVAL RULES

    def apply_removal_rules(self, scraped_torrents, history_data=None):
        if history_data is None:
            history_data = {}

        stalled = []
        logging.info("Applying pre-match rules to scraped torrents...")

        for t in scraped_torrents:
            reason = None
            days = t['days_seeding']
            uploaded = t['uploaded_mb']
            size = t['size_mb']
            tid = t['id']

            if days >= 8 and uploaded <= 1024:
                reason = "7+ days with ≤1GB uploaded"
            elif days >= 9 and uploaded < 2560:
                reason = "9+ days with <2.5GB uploaded"
            elif days >= 11 and uploaded < 5120:
                reason = "11+ days with <5GB uploaded"
            elif days >= 13 and uploaded < 10240:
                reason = "13+ days with <10GB uploaded"
            elif days >= 17 and uploaded < 15360:
                reason = "17+ days with <15GB uploaded"
            elif days >= 20:
                torrent_history = history_data.get(tid)
                if torrent_history and 'snapshots' in torrent_history and len(torrent_history['snapshots']) > 1:
                    lookback_date = datetime.now() - timedelta(days=7)
                    last_snapshot = torrent_history['snapshots'][-1]
                    past_snapshot = next(
                        (s for s in reversed(torrent_history['snapshots'])
                         if datetime.strptime(s['date'], "%Y-%m-%d") <= lookback_date),
                        None
                    )
                    if past_snapshot:
                        growth_mb = last_snapshot['uploaded_mb'] - past_snapshot['uploaded_mb']
                        threshold_mb = size * 0.01
                        if growth_mb < threshold_mb:
                            reason = "20+ days with <1% growth in last 7 days"
                        else:
                            logging.debug(f"HIGH PERFORMER kept: '{t['name'][:50]}...' - grew {self.format_size(growth_mb)} in 7 days")

            if reason:
                logging.info(f"STALLED: '{t['name']}' - {reason}")
                logging.info(f"  └─ Days: {days} | Uploaded: {self.format_size(uploaded)} | Size: {self.format_size(size)}")
                t['reason'] = reason
                stalled.append(t)

        logging.info(f"\n{'='*80}")
        logging.info(f"STALLED TORRENT DETECTION SUMMARY:")
        logging.info(f"  Total torrents scraped: {len(scraped_torrents)}")
        logging.info(f"  Torrents flagged as stalled: {len(stalled)}")
        logging.info(f"{'='*80}\n")

        return stalled

    # MATCHING & DELETION

    def _get_tl_torrents_from_qbit(self, all_torrents):
        tl_torrents = []
        cross_seeded = 0
        for torrent in all_torrents:
            is_tl = False
            is_dc = False
            for tracker in torrent.trackers:
                tracker_url = tracker.get('url', '').lower()
                if any(p in tracker_url for p in self.TL_TRACKER_PATTERNS):
                    is_tl = True
                if any(p in tracker_url for p in self.DC_TRACKER_PATTERNS):
                    is_dc = True

            if is_tl and not is_dc:
                tl_torrents.append(torrent)
            elif is_tl and is_dc:
                cross_seeded += 1

        if cross_seeded > 0:
            logging.info(f"Excluded {cross_seeded} cross-seeded torrents")
        logging.info(f"Found {len(tl_torrents)} TorrentLeech-only torrents in qBittorrent.")
        return tl_torrents

    def _find_exact_qbit_match(self, site_name, qbit_torrents):
        for torrent in qbit_torrents:
            if torrent.name == site_name:
                return torrent
        return None

    def _is_protected_torrent(self, qbit_torrent):
        category = getattr(qbit_torrent, 'category', '')
        tags = getattr(qbit_torrent, 'tags', '')

        is_protected_category = (category == '1_year_torrents')
        tag_list = [tag.strip().lower() for tag in tags.split(',') if tag.strip()]
        is_protected_tag = 'dnt' in tag_list or 'do-not-touch' in tag_list

        is_protected = is_protected_category or is_protected_tag

        if is_protected:
            reasons = []
            if is_protected_category:
                reasons.append("category='1_year_torrents'")
            if is_protected_tag:
                reasons.append("has 'DNT' tag")
            logging.critical(f"🛡️ PROTECTED TORRENT: '{qbit_torrent.name}' - {' AND '.join(reasons)}")

        return is_protected

    def _process_stalled_torrents(self, stalled_torrents):
        # Run even when the new stalled list is empty — queues may have work.
        logging.info(
            f"Found {len(stalled_torrents)} stalled torrent(s). "
            f"Retry queue: {self.retry_queue.stats()}, "
            f"Recovery queue: {self.recovery_queue.stats()}"
        )

        removed = []
        failed_matches = []
        hashes_to_delete = []
        recovered_count = 0
        retried_count = 0

        try:
            qbt_client = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
            qbt_client.auth_log_in()
            all_qbit_torrents = qbt_client.torrents_info()

            tl_qbit_torrents = self._get_tl_torrents_from_qbit(all_qbit_torrents)
            logging.info(f"Fetched {len(tl_qbit_torrents)} TL torrents for matching.")

            # Sync lifecycle before matching so new arrivals are tracked
            self.lifecycle.bulk_sync_from_qbit(all_qbit_torrents)

            # ── Phase 0a: drain recovery queue (confirmed bad-match re-matches) ──
            if stalled_torrents:
                recovered_entries, recovery_failed = self._drain_recovery_queue(
                    stalled_torrents, tl_qbit_torrents, tracker_tag="TL"
                )
                for entry in recovered_entries:
                    qt = entry.pop('_qbit_torrent')
                    if self._is_protected_torrent(qt):
                        continue
                    self._commit_tl_match(entry, qt, hashes_to_delete, removed)
                    recovered_count += 1

            # ── Phase 0b: drain retry queue (previously failed matches) ──
            already_scheduled = set(hashes_to_delete)
            retried_entries, _ = self._drain_retry_queue(
                tl_qbit_torrents, tracker_tag="TL", exclude_hashes=already_scheduled
            )
            for entry in retried_entries:
                qt = entry.pop('_qbit_torrent')
                if self._is_protected_torrent(qt) or qt.hash in already_scheduled:
                    continue
                self._commit_tl_match(entry, qt, hashes_to_delete, removed)
                already_scheduled.add(qt.hash)
                retried_count += 1

            # ── Phase 1: normal matching for this run's stalled torrents ──
            for tl_torrent in stalled_torrents:
                matched = self._find_exact_qbit_match(tl_torrent['name'], tl_qbit_torrents)
                match_method = "exact"
                match_score = 1.0

                if not matched:
                    result = self.matcher.find_best_match(
                        tl_torrent['name'], tl_qbit_torrents,
                        tracker_tag="TL", tracker_size_mb=tl_torrent['size_mb']
                    )
                    matched, match_score, match_method = result
                    match_score = match_score or 0.0

                if matched:
                    if self._is_protected_torrent(matched):
                        continue
                    if matched.hash in already_scheduled:
                        # Already being deleted by retry/recovery drain — skip
                        continue

                    is_tl = False
                    for tracker in matched.trackers:
                        if any(p in tracker.get('url', '').lower()
                               for p in self.TL_TRACKER_PATTERNS):
                            is_tl = True
                            break

                    if not is_tl:
                        logging.critical(
                            f"  CRITICAL: '{matched.name}' FAILED final tracker check."
                        )
                        failed_matches.append(tl_torrent)
                        continue

                    tl_torrent['match_method'] = match_method
                    tl_torrent['match_score']  = match_score
                    self._commit_tl_match(tl_torrent, matched, hashes_to_delete, removed)
                    already_scheduled.add(matched.hash)
                else:
                    tl_torrent['size_mb'] = tl_torrent.get('size_mb', 0.0)
                    failed_matches.append(tl_torrent)
                    logging.warning(
                        f"  Failed to match: '{tl_torrent['name'][:60]}' "
                        f"(method={match_method})"
                    )
                    # Push to retry queue for follow-up on the next cron runs
                    self.retry_queue.push(
                        tracker_name=tl_torrent['name'],
                        tracker_tag="TL",
                        tracker_size_mb=tl_torrent.get('size_mb'),
                        failure_reason=match_method,
                    )

            if hashes_to_delete:
                logging.info(f"Deleting {len(hashes_to_delete)} torrents from qBittorrent...")
                qbt_client.torrents_delete(torrent_hashes=hashes_to_delete, delete_files=True)
                logging.info("Bulk delete sent.")

            logging.info(f"\n{'='*80}")
            logging.info(f"MATCHING & REMOVAL SUMMARY:")
            logging.info(f"  Stalled processed:   {len(stalled_torrents)}")
            logging.info(f"  Recovery queue hits: {recovered_count}")
            logging.info(f"  Retry queue hits:    {retried_count}")
            logging.info(f"  Matched & removed:   {len(removed)}")
            logging.info(f"  Failed to match:     {len(failed_matches)}")
            logging.info(f"  Pending retries:     {self.retry_queue.stats()['pending']}")
            logging.info(f"{'='*80}\n")

            qbt_client.auth_log_out()

        except Exception as e:
            logging.error(f"Error during qBittorrent interaction: {e}")
            import traceback
            logging.error(traceback.format_exc())

        self._cache_removed_storage(removed, failed_matches)
        self.matcher.save_match_data()
        self._send_consolidated_notification(removed, failed_matches)

    def _commit_tl_match(self, tracker_torrent_dict, qbit_torrent,
                         hashes_to_delete, removed_list):
        """
        Finalise a confirmed TL match: update the tracker dict, append to
        removed_list, schedule the qBit hash for deletion, and write to
        lifecycle + removal cache.
        """
        match_method = tracker_torrent_dict.get('match_method', 'unknown')
        match_score  = tracker_torrent_dict.get('match_score', 0.0)
        qbit_size_mb = qbit_torrent.size / (1024 * 1024)

        hashes_to_delete.append(qbit_torrent.hash)
        tracker_torrent_dict['size_mb'] = qbit_size_mb
        removed_list.append(tracker_torrent_dict)

        self.lifecycle.on_removal(
            qbit_hash=qbit_torrent.hash,
            qbit_name=qbit_torrent.name,
            reason=tracker_torrent_dict.get('reason', 'stalled'),
            scraper='TorrentLeech',
            tracker_name=tracker_torrent_dict['name'],
            match_score=match_score,
            match_method=match_method,
            size_mb=qbit_size_mb,
        )
        self._log_removal_to_cache(tracker_torrent_dict, qbit_torrent,
                                   match_method, match_score)
        logging.info(
            f"  Matched ({match_method}, score={match_score:.3f}): "
            f"'{tracker_torrent_dict['name'][:60]}'"
        )

    def _send_consolidated_notification(self, removed_list, failed_list):
        if not self.webhook_url:
            return
        embeds = []
        MAX_DESC = 4096

        if removed_list:
            desc = "\n".join([
                f"• {t['name']} **({self.format_size(t['size_mb'])})**"
                for t in removed_list
            ])
            if len(desc) > MAX_DESC:
                desc = desc[:MAX_DESC - 80] + "\n\n... (truncated)"

            total_size = sum(t['size_mb'] for t in removed_list)
            embeds.append({
                "title": f"✅ Successfully Removed {len(removed_list)} Stalled Torrents",
                "description": desc,
                "color": 3066993,
                "fields": [{"name": "Total Storage Saved", "value": self.format_size(total_size), "inline": False}],
                "footer": {"text": "Torrents met seeding time/ratio requirements."}
            })

        if failed_list:
            desc = "\n".join([
                f"• {t['name']} **({self.format_size(t['size_mb'])})**"
                for t in failed_list
            ])
            if len(desc) > MAX_DESC:
                desc = desc[:MAX_DESC - 80] + "\n\n... (truncated)"

            total_size = sum(t['size_mb'] for t in failed_list)
            embeds.append({
                "title": f"⚠️ Failed to Match {len(failed_list)} Stalled Torrents",
                "description": desc,
                "color": 15158332,
                "fields": [{"name": "Total Size", "value": self.format_size(total_size), "inline": True}]
            })

        if embeds:
            self._send_discord(embeds, username="TorrentLeech Monitor")

    # HELPERS

    def _parse_days(self, time_str):
        try:
            normalized = re.sub(r'\s+', ' ', time_str.strip())
            match = re.search(r'(\d+)\s*days?', normalized, re.IGNORECASE)
            return int(match.group(1)) if match else 0
        except:
            return 0

    # MAIN RUN

    def run(self):
        logging.info("=" * 80)
        logging.info("STARTING TORRENTLEECH MONITOR (COOKIE AUTH)")
        logging.info("=" * 80)

        cookies, user_agent = CookieManager.load_from_json(str(self.cookie_file))
        if not cookies:
            logging.error("No cookies found in tl_cookie.json! Exiting.")
            return

        try:
            self._init_driver(cookies, user_agent)
        except Exception as e:
            logging.error(f"Failed to initialize Chrome driver: {e}")
            import traceback
            logging.error(traceback.format_exc())
            self._send_error_notification(str(e), context="TorrentLeech Monitor")
            return

        try:
            if not self._login(cookies):
                return

            # 1. Fix HnRs
            self.fix_hnr()

            # 2. Check admin deletions
            admin_removed, admin_failed = self._check_and_process_admin_deletions()
            self._cache_removed_storage(admin_removed, admin_failed)

            # 3. Scrape seeding torrents
            longterm_ids = self._load_longterm_ids()
            history_data = self._load_history()
            scraped = self.scrape_seeding_torrents(longterm_ids)

            # 4. Update history
            history_data = self._update_history_snapshots(scraped, history_data)
            self._save_history(history_data)

            # 5. Apply rules
            stalled = self.apply_removal_rules(scraped, history_data)

            # 6. Process stalled torrents (also drains retry + recovery queues)
            self._process_stalled_torrents(stalled)

        except Exception as e:
            logging.error(f"Unhandled error in run(): {e}")
            import traceback
            logging.error(traceback.format_exc())
            self._take_screenshot()
            self._send_error_notification(str(e), context="TorrentLeech Monitor")
        finally:
            if self.driver:
                self.driver.quit()
            logging.info("Driver quit.")

if __name__ == "__main__":
    monitor = TorrentLeechMonitor()
    monitor.run()

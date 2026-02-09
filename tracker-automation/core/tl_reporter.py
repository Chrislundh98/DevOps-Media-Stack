import logging
import os
import sys
import re
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from qbittorrentapi import Client, APIConnectionError

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import CookieManager, CloudflareBypass

# --- Selectors ---
LOGGED_IN_PROOF_SELECTOR = (By.CSS_SELECTOR, "a[href*='/user/profile']")
RATIO_SELECTOR = (By.CSS_SELECTOR, "div[title='Ratio']")
HNR_SELECTOR = (By.CSS_SELECTOR, "div[title='Hit and Run'] span.link")
POINTS_SELECTOR = (By.CSS_SELECTOR, "span.total-TL-points")
STREAK_SELECTOR = (By.XPATH, "//td[contains(text(), 'Visit consecutively for 1 year')]/following-sibling::td[1]")
TABLE_ROWS_SELECTOR = (By.CSS_SELECTOR, "table#profile-seedingTable tbody tr")
NEXT_BUTTON_SELECTOR = (By.CSS_SELECTOR, "li#profile-seedingTable_next a")
NEXT_BUTTON_LI_SELECTOR = (By.ID, "profile-seedingTable_next")
NOTIFICATIONS_TABLE_SELECTOR = (By.ID, "notificationsTable")
NOTIFICATIONS_INFO_SELECTOR = (By.ID, "notificationsTable_info")


class StatsReporter:

    def __init__(self):
        base_dir = Path(__file__).parent.parent
        self.log_dir = base_dir / 'logs' / 'torrentleech' / 'stats'
        self.storage_dir = base_dir / 'storage' / 'json'
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()

        self.username = os.getenv('USERNAME')
        self.webhook_url = os.getenv('DISCORD_STATS_HOOK')
        self.qbit_url = os.getenv('QBIT_URL')
        self.qbit_user = os.getenv('QBIT_USER')
        self.qbit_pass = os.getenv('QBIT_PASS')
        self.driver = None
        self._cookies = []

        self.cookie_file = self.storage_dir / 'tl_cookie.json'
        self.history_path = self.storage_dir / 'seeding_history.json'
        self.notification_cache_path = self.storage_dir / 'notification_cache.json'
        self.accuracy_log_file = self.storage_dir / 'accuracy_log.json'
        self.daily_saved_cache = self.storage_dir / 'daily_saved_storage.json'

        self.base_url = "https://www.torrentleech.org/"
        self.browse_url = "https://www.torrentleech.org/torrents/browse"
        self.seeding_url = f"https://www.torrentleech.org/profile/{self.username}/seeding"
        self.achievements_url = f"https://www.torrentleech.org/profile/{self.username}/achievements"
        self.notifications_url = f"https://www.torrentleech.org/profile/{self.username}/notifications"

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_dir / 'stats_reporter.log'),
                logging.StreamHandler()
            ]
        )

    def _create_driver(self):
        cookies, user_agent = CookieManager.load_from_json(str(self.cookie_file))
        if not cookies:
            logging.error("No cookies found. Exiting.")
            return False
        if user_agent:
            logging.info("Using custom User-Agent from cookie file")

        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--disable-translate")
        chrome_options.add_argument("--metrics-recording-only")
        chrome_options.add_argument("--mute-audio")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--safebrowsing-disable-auto-update")
        chrome_options.add_argument("--force-device-scale-factor=1")
        if user_agent:
            chrome_options.add_argument(f"--user-agent={user_agent}")
        chrome_options.page_load_strategy = 'normal'

        self.driver = uc.Chrome(options=chrome_options, version_main=141)
        self.driver.set_page_load_timeout(90)
        self.driver.set_script_timeout(60)
        self._cookies = cookies
        return True

    def _authenticate(self):
        try:
            self.driver.get(self.base_url + "robots.txt")
        except:
            pass

        for c in self._cookies:
            c_dict = {
                'name': c['name'], 'value': c['value'],
                'domain': c.get('domain', '.torrentleech.org'),
                'path': '/', 'secure': c.get('secure', True)
            }
            if not c_dict['domain'].startswith('.'):
                c_dict['domain'] = '.' + c_dict['domain']
            try:
                self.driver.add_cookie(c_dict)
            except Exception as e:
                logging.debug(f"Failed to add cookie {c['name']}: {e}")

        logging.info("Cookies injected. Checking access...")

        self.driver.get(self.base_url)
        time.sleep(3)

        if not CloudflareBypass.handle_challenge(self.driver):
            logging.error("Failed to bypass Cloudflare")
            return False

        if "login" in self.driver.current_url.lower():
            logging.error("Cookie auth failed - redirected to login page")
            return False

        logging.info("Cookie authentication successful!")
        return True

    def _get_element_text_safe(self, selector, wait_time=10):
        try:
            element = WebDriverWait(self.driver, wait_time).until(EC.visibility_of_element_located(selector))
            return element.text.strip()
        except:
            return "N/A"

    # ======================================================================
    # MATCH ACCURACY
    # ======================================================================

    def _get_match_accuracy(self):
        if not self.accuracy_log_file.exists():
            return {'all_time': 'N/A', 'last_30_days': 'N/A', 'last_7_days': 'N/A', 'trend': '➡️', 'trend_text': 'No data'}
        try:
            with open(self.accuracy_log_file, 'r') as f:
                data = json.load(f)
            total_a = data.get("total_attempts", 0)
            total_s = data.get("total_successes", 0)
            all_time = f"{(total_s / total_a) * 100:.1f}%" if total_a > 0 else "N/A"

            sessions = data.get('sessions', [])
            if not sessions:
                return {'all_time': all_time, 'last_30_days': all_time, 'last_7_days': all_time, 'trend': '➡️', 'trend_text': 'No sessions'}

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
            logging.error(f"Failed to calculate match accuracy: {e}", exc_info=True)
            return {'all_time': 'Error', 'last_30_days': 'Error', 'last_7_days': 'Error', 'trend': '❌', 'trend_text': 'Error'}

    # ======================================================================
    # SAVED STORAGE
    # ======================================================================

    def _get_and_clear_saved_storage(self):
        if not self.daily_saved_cache.exists():
            return self._format_megabytes(0.0)
        try:
            with open(self.daily_saved_cache, 'r') as f:
                daily_log = json.load(f)
        except (json.JSONDecodeError, IOError):
            daily_log = {}

        total = sum(e.get('size_mb', 0.0) for e in daily_log.values())
        try:
            with open(self.daily_saved_cache, 'w') as f:
                json.dump({}, f)
            logging.info(f"Cleared daily saved storage cache ({len(daily_log)} entries, {self._format_megabytes(total)})")
        except IOError as e:
            logging.error(f"Failed to clear cache: {e}")
        return self._format_megabytes(total)

    # ======================================================================
    # NOTIFICATIONS
    # ======================================================================

    def _load_notification_cache(self):
        if not self.notification_cache_path.exists():
            return None
        try:
            with open(self.notification_cache_path, 'r') as f:
                return json.load(f)
        except:
            return None

    def _save_notification_cache(self, entry_count, timestamp, message):
        try:
            with open(self.notification_cache_path, 'w') as f:
                json.dump({'entry_count': entry_count, 'last_timestamp': timestamp, 'last_message': message}, f)
        except IOError as e:
            logging.error(f"Failed to save notification cache: {e}")

    def _check_notifications(self):
        logging.info("Checking notifications...")
        try:
            self.driver.get(self.notifications_url)
            time.sleep(2)
            if not CloudflareBypass.handle_challenge(self.driver):
                return None

            try:
                info_elem = WebDriverWait(self.driver, 10).until(EC.presence_of_element_located(NOTIFICATIONS_INFO_SELECTOR))
                match = re.search(r'of\s+([\d,]+)\s+entries', info_elem.text)
                current_count = int(match.group(1).replace(',', '')) if match else 0
            except:
                current_count = 0

            cached = self._load_notification_cache()
            if cached and current_count == cached.get('entry_count', 0):
                logging.info("No new notifications.")
                return None

            try:
                table = self.driver.find_element(*NOTIFICATIONS_TABLE_SELECTOR)
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
                if rows:
                    cells = rows[0].find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 2:
                        ts = cells[0].text.strip()
                        msg = cells[1].text.strip()
                        self._save_notification_cache(current_count, ts, msg)
                        logging.info(f"New notification: {msg[:100]}")
                        return {'timestamp': ts, 'message': msg}
            except Exception as e:
                logging.error(f"Error scraping notification: {e}")

            if current_count > 0:
                self._save_notification_cache(current_count, "", "")
            return None
        except Exception as e:
            logging.error(f"Error checking notifications: {e}")
            return None

    # ======================================================================
    # HISTORY & SEEDING
    # ======================================================================

    def _load_history(self):
        if not self.history_path.exists():
            return {"all_time_champion": None, "torrents": {}}
        try:
            with open(self.history_path, 'r') as f:
                data = json.load(f)
                if "all_time_champion" not in data:
                    return {"all_time_champion": None, "torrents": data}
                return data
        except json.JSONDecodeError:
            return {"all_time_champion": None, "torrents": {}}

    def _update_and_save_history(self, history_data, current_data):
        today_str = datetime.now().strftime("%Y-%m-%d")
        champ = history_data.get("all_time_champion")
        champ_mb = champ['uploaded_mb'] if champ else 0

        for tid, d in current_data.items():
            if d['uploaded_mb'] > champ_mb:
                champ = {"torrent_id": tid, "name": d['name'], "uploaded_mb": d['uploaded_mb'],
                         "uploaded_str": d['uploaded_str'], "last_seen": today_str}
                champ_mb = d['uploaded_mb']

        if champ:
            cid = champ.get('torrent_id')
            if cid in current_data:
                champ['last_seen'] = today_str
                if current_data[cid]['uploaded_mb'] > champ['uploaded_mb']:
                    champ['uploaded_mb'] = current_data[cid]['uploaded_mb']
                    champ['uploaded_str'] = current_data[cid]['uploaded_str']

        updated = {}
        old = history_data.get("torrents", {})
        for tid, d in current_data.items():
            if tid in old:
                snaps = old[tid].get('snapshots', [])
                snaps.append({"date": today_str, "uploaded_mb": d['uploaded_mb']})
                snaps = [s for s in snaps if datetime.strptime(s['date'], "%Y-%m-%d") > datetime.now() - timedelta(days=45)]
                updated[tid] = d
                updated[tid]['snapshots'] = snaps
            else:
                updated[tid] = d
                updated[tid]['snapshots'] = [{"date": today_str, "uploaded_mb": d['uploaded_mb']}]

        with open(self.history_path, 'w') as f:
            json.dump({"all_time_champion": champ, "torrents": updated}, f, indent=4)
        logging.info(f"Updated history ({len(updated)} torrents)")
        return champ

    def _scrape_all_seeding_torrents(self):
        logging.info("Scraping all seeding torrents...")
        self.driver.get(self.seeding_url)
        if not CloudflareBypass.handle_challenge(self.driver):
            return {}

        all_torrents = {}
        page = 1
        try:
            WebDriverWait(self.driver, 20).until(lambda d: len(d.find_elements(*TABLE_ROWS_SELECTOR)) > 0)
        except TimeoutException:
            logging.warning("Table empty or failed to load.")
            return {}

        while True:
            logging.info(f"--- Scraping Page {page} ---")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            rows = self.driver.find_elements(*TABLE_ROWS_SELECTOR)
            first_text = rows[0].text if rows else ""

            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 5:
                        continue
                    url = cells[0].find_element(By.TAG_NAME, 'a').get_attribute('href')
                    tid = url.split('/torrent/')[1]
                    all_torrents[tid] = {
                        "name": cells[0].text,
                        "total_size_mb": self._parse_size_to_mb(cells[1].text),
                        "uploaded_str": cells[4].text,
                        "uploaded_mb": self._parse_size_to_mb(cells[4].text)
                    }
                except:
                    continue

            try:
                next_li = self.driver.find_element(*NEXT_BUTTON_LI_SELECTOR)
                if "disabled" in next_li.get_attribute("class"):
                    break
                self.driver.execute_script("arguments[0].click();", self.driver.find_element(*NEXT_BUTTON_SELECTOR))
                WebDriverWait(self.driver, 10).until(lambda d: d.find_elements(*TABLE_ROWS_SELECTOR)[0].text != first_text)
                page += 1
            except:
                break

        logging.info(f"Total seeding torrents scraped: {len(all_torrents)}")
        return all_torrents

    def _calculate_hottest_seeder(self, current, old):
        info = {'name': 'N/A', 'diff': 'N/A'}
        if not old:
            return info
        today = datetime.now().strftime("%Y-%m-%d")
        max_diff, best = -1.0, "N/A"

        for tid, cur in current.items():
            if tid not in old:
                continue
            snaps = old[tid].get('snapshots', [])
            if not snaps:
                continue
            comp = None
            for s in reversed(snaps):
                if s.get('date') != today:
                    comp = s; break
            if not comp and snaps:
                comp = snaps[0]
            if comp:
                d = cur['uploaded_mb'] - comp.get('uploaded_mb', 0)
                if d > max_diff:
                    max_diff, best = d, cur['name']

        if max_diff > 0.1:
            info['name'] = best
            info['diff'] = f"{max_diff / 1024:.2f} GB" if max_diff > 1024 else f"{max_diff:.2f} MB"
        return info

    @staticmethod
    def _format_megabytes(mb):
        if mb <= 0: return "0 MB"
        if mb < 1: return f"{mb * 1024:.2f} KB"
        if mb < 1024: return f"{mb:.2f} MB"
        if mb < 1024 * 1024: return f"{mb / 1024:.2f} GB"
        return f"{mb / (1024 * 1024):.2f} TB"

    @staticmethod
    def _parse_size_to_mb(s):
        s = s.lower().strip()
        try:
            v = float(s.split()[0].replace(',', ''))
            if "gb" in s: return v * 1024
            if "tb" in s: return v * 1024 * 1024
            if "kb" in s: return v / 1024
            return v
        except:
            return 0.0

    # ======================================================================
    # DISCORD
    # ======================================================================

    def _send_discord_notification(self, ratio, hnr_count, points, streak, top, hottest, champ, accuracy, saved, notif=None, achievement=False):
        if not self.webhook_url:
            logging.error("DISCORD_STATS_HOOK not set")
            return

        title = "📊 Daily TorrentLeech Stats Report"
        parts = [
            f"**📈 Ratio:** `{ratio}`",
            "",
            f"**🎯 Match Accuracy:**",
            f"  • 7-Day: `{accuracy['last_7_days']}` {accuracy['trend']} `{accuracy['trend_text']}`",
            f"  • 30-Day: `{accuracy['last_30_days']}`",
            f"  • All-Time: `{accuracy['all_time']}`",
            f"**💾 Total Storage Saved (24h):** `{saved}`",
            "",
            f"**🗓️ Consecutive Day Streak:** `{streak}`",
            f"**💰 TL Points:** `{points}`"
        ]

        if notif:
            notif_ts = notif.get('timestamp', '').strip()
            notif_msg = notif.get('message', '').strip()
            if notif_ts or notif_msg:
                parts.append("")
                parts.append("⚠️ **New Notification Received!**")
                if notif_ts:
                    parts.append(f"**📅 Date:** `{notif_ts}`")
                if notif_msg:
                    if len(notif_msg) > 500:
                        notif_msg = notif_msg[:497] + "..."
                    parts.append(f"**📨 Message:** {notif_msg}")

        if achievement:
            parts.append("")
            parts.append("🎉 **Achievement Unlocked!** 🎉")
            parts.append("All `1_year_torrents` have been seeding for over 365 days!")

        if top and top['name'] != 'N/A':
            parts.append("")
            parts.append(f"**🏆 Top Seeder (Current):** `{top['name']}`")
            parts.append(f"**⬆️ Total Uploaded:** `{top['uploaded']}`")

        if champ and champ['name'] != 'N/A':
            parts.append("")
            parts.append(f"**👑 All-Time Champion:** `{champ['name']}`")
            parts.append(f"**📦 Uploaded:** `{champ['uploaded']}`")
            if champ.get('last_seen'):
                parts.append(f"**📅 Last Seen:** `{champ['last_seen']}`")

        if hottest and hottest['name'] != 'N/A':
            parts.append("")
            parts.append(f"**🔥 Hottest Seeder (24h):** `{hottest['name']}`")
            parts.append(f"**💨 Uploaded:** `{hottest['diff']}`")

        hnr_warn = False
        try:
            hnr_warn = int(hnr_count) > 0
        except (ValueError, TypeError):
            if isinstance(hnr_count, str) and hnr_count != "0":
                hnr_warn = True

        if hnr_warn:
            title = f"🚨 HnR WARNING ({hnr_count}) 🚨"
            parts.append("")
            parts.append(f"**🚨 Warning:** You have `{hnr_count}` Hit & Runs!")

        data = {
            "username": "TorrentLeech Stats",
            "avatar_url": "https://www.torrentleech.org/favicon.ico",
            "embeds": [{
                "title": title,
                "description": "\n".join(parts),
                "color": 15158332 if hnr_warn else 3066993
            }]
        }

        logging.info("Sending stats notification to Discord...")
        try:
            requests.post(self.webhook_url, data=json.dumps(data),
                          headers={"Content-Type": "application/json"}).raise_for_status()
            logging.info("Discord notification sent successfully!")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Discord notification: {e}")

    # ======================================================================
    # RUN
    # ======================================================================

    def run(self):
        if not self._create_driver():
            return
        try:
            # Achievement check
            achievement = False
            try:
                qbt = Client(host=self.qbit_url, username=self.qbit_user, password=self.qbit_pass)
                qbt.auth_log_in()
                yr_ago = time.time() - 365 * 86400
                ach_t = qbt.torrents_info(category="1_year_torrents")
                if len(ach_t) >= 1000 and all(not (hasattr(t, 'completion_on') and t.completion_on > yr_ago) for t in ach_t):
                    achievement = True
                qbt.auth_log_out()
            except Exception as e:
                logging.error(f"Achievement check failed: {e}")

            if not self._authenticate():
                return

            notif = self._check_notifications()

            self.driver.get(self.browse_url)
            if not CloudflareBypass.handle_challenge(self.driver):
                return

            ratio = self._get_element_text_safe(RATIO_SELECTOR)
            hnr_text = self._get_element_text_safe(HNR_SELECTOR)
            hnr_count = 0
            if hnr_text != "N/A":
                m = re.search(r'\d+', hnr_text)
                if m:
                    try: hnr_count = int(m.group())
                    except: pass
            points = self._get_element_text_safe(POINTS_SELECTOR)

            history = self._load_history()
            current = self._scrape_all_seeding_torrents()

            top_info = {'name': 'N/A', 'uploaded': 'N/A'}
            if current:
                t = max(current.values(), key=lambda x: x['uploaded_mb'])
                top_info = {'name': t['name'], 'uploaded': t['uploaded_str']}

            hottest = self._calculate_hottest_seeder(current, history.get("torrents", {}))
            champ = self._update_and_save_history(history, current)
            champ_info = {'name': 'N/A', 'uploaded': 'N/A'}
            if champ:
                champ_info = {'name': champ['name'], 'uploaded': champ['uploaded_str'],
                              'last_seen': champ.get('last_seen', 'Unknown')}

            streak = "N/A"
            try:
                self.driver.get(self.achievements_url)
                if CloudflareBypass.handle_challenge(self.driver):
                    el = WebDriverWait(self.driver, 10).until(EC.presence_of_element_located(STREAK_SELECTOR))
                    streak = el.text.strip().split(' ')[0]
            except Exception as e:
                logging.error(f"Streak scrape failed: {e}")

            accuracy = self._get_match_accuracy()
            saved = self._get_and_clear_saved_storage()

            self._send_discord_notification(ratio, hnr_count, points, streak,
                                            top_info, hottest, champ_info,
                                            accuracy, saved, notif, achievement)
        except Exception as e:
            logging.error(f"Unhandled error: {e}", exc_info=True)
        finally:
            if self.driver:
                self.driver.quit()
            logging.info("Driver quit.")


if __name__ == "__main__":
    tracker = sys.argv[1] if len(sys.argv) > 1 else 'torrentleech'
    if tracker == 'digitalcore':
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.info("DigitalCore stats reporter not yet migrated to new structure.")
        logging.info("Run the old dc_daily_report.py directly if needed.")
    else:
        reporter = StatsReporter()
        reporter.run()

import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import CookieManager, CloudflareBypass
from lib.notifications import DiscordNotifier

class AchievementTracker:

    def __init__(self, tracker='torrentleech'):
        base_dir = Path(__file__).parent.parent
        self.tracker = tracker.lower()
        self.log_dir = base_dir / 'logs' / self.tracker / 'achievements'
        self.storage_dir = base_dir / 'storage' / 'json'

        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._setup_logging()

        self.username = os.getenv('USERNAME')
        self.notifier = DiscordNotifier(hook_type='stats')
        self.driver = None

        if self.tracker == 'torrentleech':
            self.cookie_file = self.storage_dir / 'tl_cookie.json'
            self.achievements_url = f"https://www.torrentleech.org/profile/{self.username}/achievements"
            self.base_url = "https://www.torrentleech.org/"
        else:
            raise ValueError(f"Unsupported tracker: {tracker}")

    def _setup_logging(self):
        log_file = self.log_dir / f'achievements_{datetime.now().strftime("%Y%m%d")}.log'

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )

    def _init_driver(self):
        cookies, user_agent = CookieManager.load_from_json(str(self.cookie_file))
        if not cookies:
            return False

        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--mute-audio")
        chrome_options.add_argument("--no-first-run")

        if user_agent:
            chrome_options.add_argument(f"--user-agent={user_agent}")

        chrome_options.page_load_strategy = 'normal'

        self.driver = uc.Chrome(options=chrome_options, version_main=141)
        self.driver.set_page_load_timeout(90)

        try:
            self.driver.get(self.base_url + "robots.txt")
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
            except:
                pass

        self.driver.get(self.base_url)
        time.sleep(3)

        CloudflareBypass.handle_challenge(self.driver)
        return True

    def check_achievements(self):
        logging.info("Checking achievements...")

        if not self._init_driver():
            return

        try:
            self.driver.get(self.achievements_url)
            time.sleep(3)

            if not CloudflareBypass.handle_challenge(self.driver):
                return

            achievements_container = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'achievements-list'))
            )

            achievements = achievements_container.find_elements(By.CLASS_NAME, 'achievement-item')

            unlocked = []
            locked = []

            for achievement in achievements:
                try:
                    title = achievement.find_element(By.CLASS_NAME, 'achievement-title').text
                    description = achievement.find_element(By.CLASS_NAME, 'achievement-description').text

                    if 'unlocked' in achievement.get_attribute('class'):
                        unlocked.append({'title': title, 'description': description})
                    else:
                        locked.append({'title': title, 'description': description})
                except:
                    continue

            logging.info(f"Unlocked: {len(unlocked)}, Locked: {len(locked)}")

            self.notifier.send_stats_report({
                'Unlocked Achievements': len(unlocked),
                'Locked Achievements': len(locked),
                'Progress': f"{len(unlocked)}/{len(unlocked) + len(locked)}"
            }, tracker_name=f"{self.tracker.title()} Achievements")

        except Exception as e:
            logging.error(f"Error checking achievements: {e}")
        finally:
            if self.driver:
                self.driver.quit()

if __name__ == "__main__":
    tracker = AchievementTracker('torrentleech')
    tracker.check_achievements()

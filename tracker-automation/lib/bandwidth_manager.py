#!/usr/bin/env python3
import requests
import time
import logging
import os
import ipaddress
from dotenv import load_dotenv

load_dotenv()

# Jellyfin
JELLYFIN_URL = os.getenv("JELLYFIN_URL")
JELLYFIN_API = os.getenv("JELLYFIN_API")

# qBittorrent
QBIT_URL = os.getenv("QBIT_URL")
QBIT_USER = os.getenv("QBIT_USER")
QBIT_PASS = os.getenv("QBIT_PASS")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))

LAN_SUBNET = os.getenv("LAN_SUBNET", "192.168.0.0/24")
LOCAL_USER = os.getenv("LOCAL_USER", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

class BandwidthManager:
    def __init__(self):
        self.session = requests.Session()
        self.qb_session = requests.Session()
        self.lan_network = ipaddress.ip_network(LAN_SUBNET)
        
        self._login_qbittorrent()

        if not JELLYFIN_URL or not JELLYFIN_API:
            logger.error("Jellyfin not configured!")
            raise Exception("JELLYFIN_URL and JELLYFIN_API required")
        
        logger.info(f"Jellyfin: {JELLYFIN_URL}")
        logger.info(f"LAN subnet: {LAN_SUBNET}, Local user: {LOCAL_USER}")

    def _login_qbittorrent(self):
        resp = self.qb_session.post(
            f"{QBIT_URL}/api/v2/auth/login",
            data={'username': QBIT_USER, 'password': QBIT_PASS},
            timeout=5
        )
        if resp.status_code not in (200, 204) or resp.text not in ("Ok.", ""):
            raise Exception("qBittorrent auth failed")
        logger.info("Authenticated with qBittorrent")

    def _is_lan_ip(self, ip_str):
        """Check if IP is within LAN subnet"""
        if not ip_str:
            return False
        try:
            # Handle IPv6-mapped IPv4 (::ffff:192.168.0.x)
            if ip_str.startswith("::ffff:"):
                ip_str = ip_str[7:]
            # Strip port if present
            if ":" in ip_str and not ip_str.startswith("["):
                ip_str = ip_str.rsplit(":", 1)[0]
            ip = ipaddress.ip_address(ip_str)
            return ip in self.lan_network
        except ValueError:
            logger.warning(f"Could not parse IP: {ip_str}")
            return False

    def get_jellyfin_streams(self):
        """
        Returns tuple: (local_user_lan_streams, other_streams)
        - local_user_lan_streams: YOUR streams from LAN (don't need throttling)
        - other_streams: Everyone else OR your remote streams (need throttling)
        """
        try:
            resp = self.session.get(
                f"{JELLYFIN_URL}/Sessions",
                headers={'X-Emby-Token': JELLYFIN_API},
                timeout=5
            )
            sessions = resp.json()
            
            local_user_lan = 0
            other = 0
            
            for s in sessions:
                if not s.get('NowPlayingItem'):
                    continue
                
                username = s.get('UserName', '')
                ip = s.get('RemoteEndPoint', '')
                is_lan = self._is_lan_ip(ip)
                
                if username.lower() == LOCAL_USER.lower() and is_lan:
                    local_user_lan += 1
                    logger.debug(f"{username} streaming from LAN ({ip}) - no throttle needed")
                else:
                    other += 1
                    location = "LAN" if is_lan else "WAN"
                    logger.debug(f"{username} streaming from {location} ({ip}) - throttle needed")
            
            return local_user_lan, other
        except Exception as e:
            logger.error(f"Jellyfin error: {e}")
            return 0, 0

    def should_throttle(self):
        """
        Returns True if we should enable alt speeds.
        
        Logic:
        - You on LAN alone = no throttle (LAN doesn't use WAN upload)
        - Anyone else streaming = throttle (protect their bandwidth)
        - You streaming remotely = throttle (uses WAN upload)
        """
        local_user_lan, other = self.get_jellyfin_streams()
        
        if local_user_lan > 0 or other > 0:
            logger.info(f"Streams - {LOCAL_USER}@LAN: {local_user_lan}, Others/Remote: {other}")
        
        return other > 0

    def get_current_mode(self):
        try:
            resp = self.qb_session.get(f"{QBIT_URL}/api/v2/transfer/speedLimitsMode", timeout=5)
            return int(resp.text.strip())
        except:
            return 0

    def set_alt_speed(self, enable):
        current_mode = self.get_current_mode()
        target_mode = 1 if enable else 0

        if current_mode != target_mode:
            self.qb_session.post(f"{QBIT_URL}/api/v2/transfer/toggleSpeedLimitsMode", timeout=5)
            logger.info(f"Alt speed {'ENABLED' if enable else 'DISABLED'}")

    def run(self):
        logger.info("Starting bandwidth manager...")
        while True:
            try:
                throttle = self.should_throttle()
                self.set_alt_speed(throttle)
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    BandwidthManager().run()
import requests
import os
import logging
from uuid import uuid4
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

FLARESOLVERR_API_URL = 'http://localhost:8191/v1'
LOGIN_ACTION_URL = "https://www.torrentleech.org/user/account/login/"
START_URL = "https://www.torrentleech.org/login.php"

class TorrentLeechClient:
    
    def __init__(self, username=None, password=None, debug_mode=False):
        self.username = username or os.getenv('USERNAME')
        self.password = password or os.getenv('PASSWORD')
        self.session_id = f"tl_session_{uuid4().hex}"
        self.debug_mode = debug_mode
        self.user_agent = None
        self.cookies = None
        
        if not self.username or not self.password:
            logger.error("USERNAME or PASSWORD not found in environment.")
            raise ValueError("Missing TorrentLeech credentials.")

    def __enter__(self):
        logger.info(f"Starting TorrentLeech session: {self.session_id}")
        
        try:
            r = requests.post(FLARESOLVERR_API_URL, json={
                "cmd": "sessions.create",
                "session": self.session_id
            }, timeout=15)
            r.raise_for_status()
            if r.json().get('status') != 'ok':
                 raise ConnectionError(f"FS session creation failed: {r.json().get('message')}")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to connect to FlareSolverr at {FLARESOLVERR_API_URL}. Is it running?")
            raise ConnectionError(f"FlareSolverr API error: {e}")

        r = self._send_flaresolverr_command(
            cmd="request.get",
            url=START_URL,
            maxTimeout=60000,
            screenshot=False 
        )
        
        r = self._send_flaresolverr_command(
            cmd="request.post",
            url=LOGIN_ACTION_URL,
            maxTimeout=60000,
            postData=f"username={self.username}&password={self.password}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            screenshot=False
        )

        if 'tluid' not in [c['name'] for c in r['cookies']]:
            logger.error(f"Login failed for session {self.session_id}. Cookies indicate failure.")
            raise Exception("TorrentLeech login failed or session cookies missing.")

        self.cookies = r['cookies']
        self.user_agent = r['userAgent']
        logger.info("Login successful. Session is ready for use.")
        
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info(f"Destroying FlareSolverr session: {self.session_id}")
        try:
            requests.post(FLARESOLVERR_API_URL, json={
                "cmd": "sessions.destroy",
                "session": self.session_id
            }, timeout=15)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to destroy FS session {self.session_id}: {e}")
        
        return False 
        
    def _send_flaresolverr_command(self, **kwargs):
        payload = kwargs
        payload['session'] = self.session_id
        
        r = requests.post(FLARESOLVERR_API_URL, json=payload, timeout=kwargs.get('maxTimeout', 60000) / 1000 + 10)
        r.raise_for_status()
        
        data = r.json()
        if data.get('status') != 'ok':
            logger.error(f"FlareSolverr command failed: {data.get('message')}")
            return {'cookies': [], 'userAgent': None, 'response': data.get('solution', {}).get('response', 'N/A')}

        return data['solution']
        
    def get_page_content(self, url, headers=None, params=None):
        if not self.cookies or not self.user_agent:
            raise Exception("Session not fully initialized. Ensure client is used within a 'with' block.")
            
        logger.info(f"Performing authenticated GET request to: {url}")
        
        cookies_dict = {c['name']: c['value'] for c in self.cookies}
        
        final_headers = headers if headers is not None else {}
        final_headers['User-Agent'] = self.user_agent
        
        response = requests.get(
            url, 
            cookies=cookies_dict, 
            headers=final_headers,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response
    
    def get_content_via_flaresolverr(self, url, timeout=60000):
        if not self.session_id:
            raise Exception("FlareSolverr session not created.")
            
        r = self._send_flaresolverr_command(
            cmd="request.get",
            url=url,
            session=self.session_id,
            maxTimeout=timeout,
            screenshot=False
        )
        
        status = r.get('status')
        if status not in [200, 304]:
             raise requests.exceptions.HTTPError(f"FlareSolverr returned status {status} for URL: {url}", response=requests.Response())

        return r['response']

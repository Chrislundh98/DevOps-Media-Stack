import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple

class CookieManager:
    
    @staticmethod
    def load_from_json(cookie_file: str) -> Tuple[List[Dict], Optional[str]]:
        if not os.path.exists(cookie_file):
            logging.error(f"Cookie file not found: {cookie_file}")
            return [], None
        
        try:
            with open(cookie_file, 'r') as f:
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
            logging.error(f"Failed to load JSON config from {cookie_file}: {e}")
            return [], None
    
    @staticmethod
    def apply_to_driver(driver, cookies: List[Dict], domain: str = None):
        for cookie in cookies:
            try:
                cookie_dict = {
                    'name': cookie['name'],
                    'value': cookie['value'],
                    'path': cookie.get('path', '/'),
                    'secure': cookie.get('secure', False),
                }
                
                if domain:
                    cookie_dict['domain'] = domain
                elif 'domain' in cookie:
                    cookie_dict['domain'] = cookie['domain']
                
                driver.add_cookie(cookie_dict)
            except Exception as e:
                logging.warning(f"Failed to add cookie {cookie.get('name', 'unknown')}: {e}")
    
    @staticmethod
    def save_to_json(cookie_file: str, cookies: List[Dict], user_agent: Optional[str] = None):
        Path(cookie_file).parent.mkdir(parents=True, exist_ok=True)
        
        data = []
        if user_agent:
            data.append({"user_agent": user_agent})
        data.extend(cookies)
        
        try:
            with open(cookie_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.info(f"Saved cookies to {cookie_file}")
        except Exception as e:
            logging.error(f"Failed to save cookies: {e}")

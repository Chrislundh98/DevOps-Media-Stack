import aiohttp
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Optional
import config


class CoCAPIError(Exception):
    """Custom exception for CoC API errors"""
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"CoC API Error {status}: {message}")


class CoCAPI:
    def __init__(self):
        self.base_url = config.COC_API_BASE
        self.headers = {
            "Authorization": f"Bearer {config.COC_API_KEY}",
            "Accept": "application/json"
        }
    
    def _encode_tag(self, tag: str) -> str:
        """URL encode a player/clan tag (# needs encoding)"""
        if not tag.startswith("#"):
            tag = f"#{tag}"
        return quote(tag)
    
    async def _request(self, endpoint: str) -> dict:
        """Make a request to the CoC API"""
        url = f"{self.base_url}{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 403:
                    raise CoCAPIError(403, "Invalid API key or IP not whitelisted")
                elif response.status == 404:
                    raise CoCAPIError(404, "Not found (clan/player doesn't exist or not in war)")
                else:
                    text = await response.text()
                    raise CoCAPIError(response.status, text)
    
    async def get_clan(self, clan_tag: str) -> dict:
        """Get clan information"""
        encoded = self._encode_tag(clan_tag)
        return await self._request(f"/clans/{encoded}")
    
    async def get_current_war(self, clan_tag: str) -> dict:
        """Get current war information"""
        encoded = self._encode_tag(clan_tag)
        return await self._request(f"/clans/{encoded}/currentwar")
    
    async def get_player(self, player_tag: str) -> dict:
        """Get player information"""
        encoded = self._encode_tag(player_tag)
        return await self._request(f"/players/{encoded}")
    
    async def get_cwl_group(self, clan_tag: str) -> dict:
        """Get CWL group information"""
        encoded = self._encode_tag(clan_tag)
        return await self._request(f"/clans/{encoded}/currentwar/leaguegroup")
    
    async def get_cwl_war(self, war_tag: str) -> dict:
        """Get specific CWL war information"""
        encoded = self._encode_tag(war_tag)
        return await self._request(f"/clanwarleagues/wars/{encoded}")


def parse_coc_timestamp(timestamp: str) -> datetime:
    """Parse CoC API timestamp format (20240115T120000.000Z) to datetime"""
    # Format: YYYYMMDDTHHmmss.SSSZ
    return datetime.strptime(timestamp, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def format_time_remaining(end_time: datetime) -> str:
    """Format time remaining until end_time as human readable string"""
    now = datetime.now(timezone.utc)
    delta = end_time - now
    
    if delta.total_seconds() <= 0:
        return "Ended"
    
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"
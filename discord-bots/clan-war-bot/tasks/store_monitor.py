import json
import re
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import discord
import aiohttp
from discord.ext import tasks

import config
import database as db

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger('warbot')

STORE_URL = "https://store.supercell.com/en/clashofclans"
COOKIES_FILE = Path(__file__).parent.parent / "data" / "store_cookies.json"

_bot: Optional["Bot"] = None

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def setup_store_monitor(bot: "Bot"):
    global _bot
    _bot = bot

def start_monitor():
    if not _store_poll.is_running():
        _store_poll.start()
        log.info("[store] Store monitor started (30 min interval)")

async def _get_channel() -> Optional[discord.TextChannel]:
    if not _bot:
        return None
    if not config.STORE_CHANNEL_ID:
        log.warning("[store] No STORE_CHANNEL_ID configured")
        return None
    channel = _bot.get_channel(int(config.STORE_CHANNEL_ID))
    if not channel:
        log.warning(f"[store] Channel {config.STORE_CHANNEL_ID} not found")
    return channel

def _load_cookies() -> dict:
    if not COOKIES_FILE.exists():
        log.warning(f"[store] Cookie file not found: {COOKIES_FILE}")
        return {}
    with open(COOKIES_FILE) as f:
        raw = json.load(f)
    return {c["name"]: c["value"] for c in raw}

async def _fetch_next_data(session: aiohttp.ClientSession) -> Optional[dict]:
    try:
        async with session.get(STORE_URL, headers=_HEADERS) as resp:
            if resp.status != 200:
                log.warning(f"[store] HTTP {resp.status} from store page")
                return None
            html = await resp.text()
    except Exception as e:
        log.error(f"[store] Fetch failed: {e}")
        return None

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.DOTALL
    )
    if not m:
        log.warning("[store] __NEXT_DATA__ not found — page structure may have changed")
        return None

    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        log.error(f"[store] Failed to parse __NEXT_DATA__: {e}")
        return None

async def _send(embed: discord.Embed):
    channel = await _get_channel()
    if not channel:
        return
    try:
        await channel.send(embed=embed)
    except Exception as e:
        log.error(f"[store] Failed to send message: {e}")

async def check_store():
    """Fetch the store, detect free items and community events, post to channel."""
    cookies = _load_cookies()
    if not cookies:
        log.warning("[store] No cookies — skipping check")
        return

    async with aiohttp.ClientSession(cookies=cookies) as session:
        next_data = await _fetch_next_data(session)

    if not next_data:
        return

    page_props = next_data.get("props", {}).get("pageProps", {})
    login_status = page_props.get("shared", {}).get("serverLoginStatus", "")

    if login_status != "LOGGED_IN":
        log.warning(
            f"[store] Session not authenticated (status: '{login_status}'). "
            "Update data/store_cookies.json with a fresh SESSION_COOKIE."
        )
        return

    props = page_props.get("props", {})
    offers = props.get("offers", [])
    await _process_community_events(props.get("communityEventData", []), offers)
    await _process_free_offers(offers)

def _find_reward_item(milestones: list, offers: list) -> Optional[str]:
    """Return the item name for a milestone reward by matching its chronosId to a free offer."""
    if not milestones:
        return None
    chronos_id = milestones[0].get("reward", {}).get("chronosId", "")
    if not chronos_id:
        return None
    # Free offer IDs are formatted as "Free:chronos:{chronosId}-0:0"
    target_prefix = f"Free:chronos:{chronos_id}"
    for offer in offers:
        data = offer.get("data", {}).get("data", {})
        if data.get("id", "").startswith(target_prefix):
            return data.get("title", {}).get("cardHeading", {}).get("en")
    return None

async def _process_community_events(events: list, offers: list):
    for event in events:
        event_key = event.get("eventKey", "")
        if not event_key:
            continue

        title = event.get("title", {}).get("en", "Unknown Event")
        description = event.get("description", {}).get("en", "")
        progress = int(event.get("progressPercent", 0))

        milestones = event.get("milestones", [])
        claim_ends_at: Optional[int] = milestones[0].get("claimEndsAt") if milestones else None

        existing = db.get_store_event(event_key)
        ends_fmt = f"<t:{claim_ends_at // 1000}:R>" if claim_ends_at else "unknown"

        if not existing:
            if progress >= 100:
                # Already complete on first sight — one message only, no "event started" noise
                db.upsert_store_event(
                    event_key, title, progress, claim_ends_at,
                    announced_start=True, announced_complete=True
                )
                reward_item = _find_reward_item(milestones, offers)
                _suppress_matching_offer(milestones, offers)
                await _send(_claim_embed(title, ends_fmt, reward_item))
                log.info(f"[store] Event already complete on first sight: {title}")
            else:
                # Event in progress — announce it's started
                db.upsert_store_event(
                    event_key, title, progress, claim_ends_at,
                    announced_start=True, announced_complete=False
                )
                embed = discord.Embed(
                    title=f"Community Event: {title}",
                    url=STORE_URL,
                    description=description,
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Claim window", value=ends_fmt, inline=True)
                embed.set_footer(text="Supercell Store")
                await _send(embed)
                log.info(f"[store] New event: {title} ({progress}%)")

        elif progress >= 100 and not existing.get("announced_complete"):
            # Event just hit 100%
            db.upsert_store_event(
                event_key, title, progress, claim_ends_at,
                announced_start=True, announced_complete=True
            )
            reward_item = _find_reward_item(milestones, offers)
            _suppress_matching_offer(milestones, offers)
            await _send(_claim_embed(title, ends_fmt, reward_item))
            log.info(f"[store] Event complete: {title}")

        else:
            db.upsert_store_event(
                event_key, title, progress, claim_ends_at,
                announced_start=bool(existing.get("announced_start")),
                announced_complete=bool(existing.get("announced_complete"))
            )

def _suppress_matching_offer(milestones: list, offers: list):
    """Pre-mark the community event's reward offer as announced to prevent double-posting."""
    if not milestones:
        return
    chronos_id = milestones[0].get("reward", {}).get("chronosId", "")
    if not chronos_id:
        return
    target_prefix = f"Free:chronos:{chronos_id}"
    for offer in offers:
        data = offer.get("data", {}).get("data", {})
        offer_id = data.get("id", "")
        if offer_id.startswith(target_prefix):
            item_title = data.get("title", {}).get("cardHeading", {}).get("en", "Free Item")
            db.mark_store_offer_announced(offer_id, item_title)
            return

def _claim_embed(title: str, ends_fmt: str, reward_item: Optional[str] = None) -> discord.Embed:
    if reward_item:
        description = (
            f"The community goal is complete. "
            f"Claim your **{reward_item}** at the Supercell Store before it expires."
        )
    else:
        description = (
            "The community goal is complete. "
            "Claim your reward at the Supercell Store before it expires."
        )
    embed = discord.Embed(
        title=f"Free Reward: {title}",
        url=STORE_URL,
        description=description,
        color=discord.Color.green(),
    )
    embed.add_field(name="Claim expires", value=ends_fmt, inline=True)
    embed.set_footer(text="Supercell Store")
    return embed

async def _process_free_offers(offers: list):
    for offer in offers:
        data = offer.get("data", {}).get("data", {})
        offer_id = data.get("id", "")
        if not offer_id:
            continue

        price = data.get("price", {})
        if price.get("USD-US", -1) != 0:
            continue

        title = data.get("title", {}).get("cardHeading", {}).get("en", "Free Item")

        if not db.has_store_offer_been_announced(offer_id):
            db.mark_store_offer_announced(offer_id, title)
            embed = discord.Embed(
                title=f"Free Item: {title}",
                url=STORE_URL,
                description=f"{title} is available for free at the Supercell Store.",
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Supercell Store")
            await _send(embed)
            log.info(f"[store] Free offer: {title} ({offer_id})")

@tasks.loop(minutes=30)
async def _store_poll():
    try:
        await check_store()
    except Exception as e:
        log.error(f"[store] Unhandled error in poll loop: {e}", exc_info=True)

@_store_poll.before_loop
async def _before_store_poll():
    if _bot:
        await _bot.wait_until_ready()

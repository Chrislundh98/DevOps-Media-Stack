import os
from dotenv import load_dotenv

load_dotenv()

# Discord
BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

# Channel IDs
REMINDER_CHANNEL_ID = os.getenv("REMINDER_CHANNEL_ID", "YOUR_CHANNEL_ID")
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID", "YOUR_CHANNEL_ID")
COLEADER_CHANNEL_ID = os.getenv("COLEADER_CHANNEL_ID", "YOUR_CHANNEL_ID")

# Clash of Clans
COC_API_KEY = os.getenv("API_KEY")
CLAN_TAG = "#YOUR_CLAN_TAG"

# API Base URL
COC_API_BASE = "https://api.clashofclans.com/v1"

# Debug mode - set to False for production
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Logging
LOG_DIR = os.getenv("LOG_DIR", "/volume1/coc-war-bot/logs")
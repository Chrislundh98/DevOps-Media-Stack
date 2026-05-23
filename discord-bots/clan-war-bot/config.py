import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

REMINDER_CHANNEL_ID = os.getenv("REMINDER_CHANNEL_ID")
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID")
COLEADER_CHANNEL_ID = os.getenv("COLEADER_CHANNEL_ID")
STORE_CHANNEL_ID = os.getenv("STORE_CHANNEL_ID", "")

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")

COC_API_KEY = os.getenv("API_KEY")
CLAN_TAG = os.getenv("CLAN_TAG")

COC_API_BASE = "https://api.clashofclans.com/v1"

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

LOG_DIR = os.getenv("LOG_DIR", "./logs")

# Ollama-backed conversational fallback for the /chat-ai command.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

# Username/ID allowed to toggle the /chat-ai beta flag. Discord ID takes
# precedence over name when both are set.
BETA_TOGGLE_USERNAME = os.getenv("BETA_TOGGLE_USERNAME", "")
BETA_TOGGLE_USER_ID = os.getenv("BETA_TOGGLE_USER_ID", "")

# Optional flavour: the clan's mascot user, occasionally @-pinged.
FAVORITE_DISCORD_USERNAME = os.getenv("FAVORITE_DISCORD_USERNAME", "")

# Per-request LLM timeout; bump if Ollama is contending with another
# CPU-heavy workload on the same host.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

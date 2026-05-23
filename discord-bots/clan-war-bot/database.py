import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime

DATABASE_PATH = Path(__file__).parent / "data" / "bot.db"

def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize database tables"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Discord users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS discord_users (
            discord_id TEXT PRIMARY KEY,
            discord_name TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # CoC accounts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coc_accounts (
            player_tag TEXT PRIMARY KEY,
            player_name TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Junction table for many-to-many relationship
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_links (
            player_tag TEXT NOT NULL,
            discord_id TEXT NOT NULL,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            linked_by TEXT,
            PRIMARY KEY (player_tag, discord_id),
            FOREIGN KEY (player_tag) REFERENCES coc_accounts(player_tag),
            FOREIGN KEY (discord_id) REFERENCES discord_users(discord_id)
        )
    """)
    
    # Sent reminders to prevent spam
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            war_id TEXT NOT NULL,
            player_tag TEXT NOT NULL,
            threshold_hours INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(war_id, player_tag, threshold_hours)
        )
    """)
    
    # War state tracking (persists across restarts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS war_state (
            war_id TEXT PRIMARY KEY,
            announced_start INTEGER DEFAULT 0,
            announced_end INTEGER DEFAULT 0,
            last_state TEXT,
            is_cwl INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # CWL season tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cwl_seasons (
            season_id TEXT PRIMARY KEY,
            clan_tag TEXT NOT NULL,
            start_date TEXT NOT NULL,
            total_days INTEGER DEFAULT 7,
            current_day INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # CWL daily performance tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cwl_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            player_tag TEXT NOT NULL,
            player_name TEXT NOT NULL,
            stars INTEGER DEFAULT 0,
            destruction REAL DEFAULT 0,
            attack_used INTEGER DEFAULT 0,
            in_roster INTEGER DEFAULT 1,
            opponent_tag TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(season_id, day_number, player_tag),
            FOREIGN KEY (season_id) REFERENCES cwl_seasons(season_id)
        )
    """)
    
    # CWL group standings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cwl_standings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id TEXT NOT NULL,
            clan_tag TEXT NOT NULL,
            clan_name TEXT NOT NULL,
            stars INTEGER DEFAULT 0,
            destruction REAL DEFAULT 0,
            wars_won INTEGER DEFAULT 0,
            wars_lost INTEGER DEFAULT 0,
            wars_tied INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(season_id, clan_tag),
            FOREIGN KEY (season_id) REFERENCES cwl_seasons(season_id)
        )
    """)
    
    # CWL bonus recommendations sent
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cwl_bonus_sent (
            season_id TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (season_id, day_number)
        )
    """)

    # Chat history for AI conversational mode (rolling window per user)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_history_user_time
        ON chat_history(user_id, created_at DESC)
    """)
    
    # Store community events tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS store_seen_events (
            event_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            progress_percent INTEGER DEFAULT 0,
            claim_ends_at INTEGER,
            announced_start INTEGER DEFAULT 0,
            announced_complete INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Store free offers tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS store_seen_offers (
            offer_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            announced INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Config table for bot settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    # Insert default config values if they don't exist
    default_config = [
        ("reminder_thresholds", "6,3,1"),
    ]
    for key, value in default_config:
        cursor.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            (key, value)
        )
    
    conn.commit()
    conn.close()
    print("[DB] Database initialized")

# Discord Users

def upsert_discord_user(discord_id: str, discord_name: str):
    """Insert or update a Discord user"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO discord_users (discord_id, discord_name, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(discord_id) DO UPDATE SET
            discord_name = excluded.discord_name,
            updated_at = CURRENT_TIMESTAMP
    """, (discord_id, discord_name))
    conn.commit()
    conn.close()

def get_discord_user(discord_id: str) -> Optional[dict]:
    """Get a Discord user by ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM discord_users WHERE discord_id = ?", (discord_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

# CoC Accounts

def upsert_coc_account(player_tag: str, player_name: str):
    """Insert or update a CoC account"""
    # Normalize tag
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO coc_accounts (player_tag, player_name, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(player_tag) DO UPDATE SET
            player_name = excluded.player_name,
            updated_at = CURRENT_TIMESTAMP
    """, (player_tag, player_name))
    conn.commit()
    conn.close()

def get_coc_account(player_tag: str) -> Optional[dict]:
    """Get a CoC account by tag"""
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coc_accounts WHERE player_tag = ?", (player_tag,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

# Account Links (Many-to-Many)

def link_account(player_tag: str, discord_id: str, linked_by: str = None) -> bool:
    """
    Link a CoC account to a Discord user.
    Returns True if link was created, False if it already existed.
    """
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO account_links (player_tag, discord_id, linked_by)
            VALUES (?, ?, ?)
        """, (player_tag, discord_id, linked_by))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def unlink_account(player_tag: str, discord_id: str) -> bool:
    """
    Unlink a CoC account from a Discord user.
    Returns True if link was removed, False if it didn't exist.
    """
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM account_links 
        WHERE player_tag = ? AND discord_id = ?
    """, (player_tag, discord_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def unlink_all_from_account(player_tag: str) -> int:
    """
    Remove all Discord links from a CoC account.
    Returns number of links removed.
    """
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM account_links WHERE player_tag = ?", (player_tag,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def get_discord_ids_for_account(player_tag: str) -> list[str]:
    """Get all Discord user IDs linked to a CoC account"""
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT discord_id FROM account_links WHERE player_tag = ?
    """, (player_tag,))
    rows = cursor.fetchall()
    conn.close()
    return [row["discord_id"] for row in rows]

def get_accounts_for_discord_user(discord_id: str) -> list[dict]:
    """Get all CoC accounts linked to a Discord user"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ca.player_tag, ca.player_name, al.linked_at
        FROM account_links al
        JOIN coc_accounts ca ON al.player_tag = ca.player_tag
        WHERE al.discord_id = ?
        ORDER BY al.linked_at
    """, (discord_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_links() -> list[dict]:
    """Get all account links with full info"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            al.player_tag,
            ca.player_name,
            al.discord_id,
            du.discord_name,
            al.linked_at
        FROM account_links al
        JOIN coc_accounts ca ON al.player_tag = ca.player_tag
        JOIN discord_users du ON al.discord_id = du.discord_id
        ORDER BY ca.player_name
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_unlinked_players_in_war(war_member_tags: list[str]) -> list[dict]:
    """
    Given a list of player tags in the current war,
    return those that have NO Discord links.
    """
    if not war_member_tags:
        return []
    
    # Normalize tags
    normalized = []
    for tag in war_member_tags:
        if not tag.startswith("#"):
            tag = f"#{tag}"
        normalized.append(tag.upper())
    
    conn = get_connection()
    cursor = conn.cursor()
    
    placeholders = ",".join("?" * len(normalized))
    cursor.execute(f"""
        SELECT ca.player_tag, ca.player_name
        FROM coc_accounts ca
        WHERE ca.player_tag IN ({placeholders})
        AND ca.player_tag NOT IN (
            SELECT player_tag FROM account_links
        )
    """, normalized)
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Sent Reminders

def has_reminder_been_sent(war_id: str, player_tag: str, threshold_hours: int) -> bool:
    """Check if a reminder has already been sent for this war/player/threshold"""
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM sent_reminders 
        WHERE war_id = ? AND player_tag = ? AND threshold_hours = ?
    """, (war_id, player_tag, threshold_hours))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def record_reminder_sent(war_id: str, player_tag: str, threshold_hours: int):
    """Record that a reminder was sent"""
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO sent_reminders (war_id, player_tag, threshold_hours)
        VALUES (?, ?, ?)
    """, (war_id, player_tag, threshold_hours))
    conn.commit()
    conn.close()

def clear_reminders_for_war(war_id: str):
    """Clear all sent reminders for a specific war"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sent_reminders WHERE war_id = ?", (war_id,))
    conn.commit()
    conn.close()

# Config

def get_config(key: str) -> Optional[str]:
    """Get a config value"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else None

def set_config(key: str, value: str):
    """Set a config value"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()

def get_reminder_thresholds() -> list[int]:
    """Get reminder thresholds as list of hours"""
    value = get_config("reminder_thresholds")
    if not value:
        return [6, 3, 1]
    return [int(x.strip()) for x in value.split(",")]

# War State (persistent across restarts)

def get_war_state(war_id: str) -> Optional[dict]:
    """Get war state by ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM war_state WHERE war_id = ?", (war_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_war_state(war_id: str, last_state: str, is_cwl: bool = False):
    """Create or update war state"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO war_state (war_id, last_state, is_cwl, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(war_id) DO UPDATE SET
            last_state = excluded.last_state,
            updated_at = CURRENT_TIMESTAMP
    """, (war_id, last_state, 1 if is_cwl else 0))
    conn.commit()
    conn.close()

def has_war_start_been_announced(war_id: str) -> bool:
    """Check if war start has been announced"""
    state = get_war_state(war_id)
    return state is not None and state.get("announced_start", 0) == 1

def mark_war_start_announced(war_id: str):
    """Mark war start as announced"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE war_state SET announced_start = 1, updated_at = CURRENT_TIMESTAMP
        WHERE war_id = ?
    """, (war_id,))
    conn.commit()
    conn.close()

def has_war_end_been_announced(war_id: str) -> bool:
    """Check if war end has been announced"""
    state = get_war_state(war_id)
    return state is not None and state.get("announced_end", 0) == 1

def mark_war_end_announced(war_id: str):
    """Mark war end as announced (upserts so it works even if record was cleaned up)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO war_state (war_id, announced_end, last_state, updated_at)
        VALUES (?, 1, 'warEnded', CURRENT_TIMESTAMP)
        ON CONFLICT(war_id) DO UPDATE SET
            announced_end = 1,
            updated_at = CURRENT_TIMESTAMP
    """, (war_id,))
    conn.commit()
    conn.close()

def cleanup_old_war_states(days: int = 7):
    """Remove war states older than specified days"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM war_state 
        WHERE updated_at < datetime('now', ?)
    """, (f'-{days} days',))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

# CWL Tracking

def get_or_create_cwl_season(clan_tag: str, season_id: str) -> dict:
    """Get or create a CWL season"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM cwl_seasons WHERE season_id = ?", (season_id,))
    row = cursor.fetchone()
    
    if row:
        conn.close()
        return dict(row)
    
    # Create new season
    cursor.execute("""
        INSERT INTO cwl_seasons (season_id, clan_tag, start_date)
        VALUES (?, ?, date('now'))
    """, (season_id, clan_tag))
    conn.commit()
    
    cursor.execute("SELECT * FROM cwl_seasons WHERE season_id = ?", (season_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row)

def update_cwl_day(season_id: str, day_number: int):
    """Update current day for a CWL season"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE cwl_seasons SET current_day = ?, updated_at = CURRENT_TIMESTAMP
        WHERE season_id = ?
    """, (day_number, season_id))
    conn.commit()
    conn.close()

def record_cwl_performance(season_id: str, day_number: int, player_tag: str, 
                           player_name: str, stars: int, destruction: float,
                           attack_used: int, in_roster: int, opponent_tag: str = None):
    """Record a player's CWL performance for a day"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cwl_performance 
        (season_id, day_number, player_tag, player_name, stars, destruction, attack_used, in_roster, opponent_tag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(season_id, day_number, player_tag) DO UPDATE SET
            player_name = excluded.player_name,
            stars = excluded.stars,
            destruction = excluded.destruction,
            attack_used = excluded.attack_used,
            in_roster = excluded.in_roster,
            opponent_tag = excluded.opponent_tag
    """, (season_id, day_number, player_tag, player_name, stars, destruction, attack_used, in_roster, opponent_tag))
    conn.commit()
    conn.close()

def get_cwl_season_performance(season_id: str) -> list[dict]:
    """Get all performance records for a CWL season, aggregated by player"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            player_tag,
            player_name,
            SUM(stars) as total_stars,
            AVG(destruction) as avg_destruction,
            SUM(attack_used) as attacks_used,
            COUNT(CASE WHEN in_roster = 1 THEN 1 END) as days_in_roster,
            COUNT(DISTINCT day_number) as days_recorded,
            SUM(CASE WHEN stars = 3 THEN 1 ELSE 0 END) as three_stars
        FROM cwl_performance
        WHERE season_id = ?
        GROUP BY player_tag
        ORDER BY total_stars DESC, avg_destruction DESC
    """, (season_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_cwl_day_performance(season_id: str, day_number: int) -> list[dict]:
    """Get performance records for a specific CWL day"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM cwl_performance
        WHERE season_id = ? AND day_number = ?
        ORDER BY stars DESC, destruction DESC
    """, (season_id, day_number))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_cwl_standings(season_id: str, clan_tag: str, clan_name: str,
                         stars: int, destruction: float, 
                         wars_won: int, wars_lost: int, wars_tied: int):
    """Update CWL standings for a clan"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cwl_standings 
        (season_id, clan_tag, clan_name, stars, destruction, wars_won, wars_lost, wars_tied)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(season_id, clan_tag) DO UPDATE SET
            clan_name = excluded.clan_name,
            stars = excluded.stars,
            destruction = excluded.destruction,
            wars_won = excluded.wars_won,
            wars_lost = excluded.wars_lost,
            wars_tied = excluded.wars_tied,
            updated_at = CURRENT_TIMESTAMP
    """, (season_id, clan_tag, clan_name, stars, destruction, wars_won, wars_lost, wars_tied))
    conn.commit()
    conn.close()

def get_cwl_standings(season_id: str) -> list[dict]:
    """Get CWL standings for a season"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM cwl_standings
        WHERE season_id = ?
        ORDER BY (wars_won * 10 + stars) DESC, destruction DESC
    """, (season_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def has_cwl_bonus_been_sent(season_id: str, day_number: int) -> bool:
    """Check if bonus recommendation has been sent for this day"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM cwl_bonus_sent WHERE season_id = ? AND day_number = ?
    """, (season_id, day_number))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def mark_cwl_bonus_sent(season_id: str, day_number: int):
    """Mark bonus recommendation as sent"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO cwl_bonus_sent (season_id, day_number)
        VALUES (?, ?)
    """, (season_id, day_number))
    conn.commit()
    conn.close()

def get_cwl_roster_changes(season_id: str) -> dict:
    """Get roster changes across CWL days"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get all players and which days they were in roster
    cursor.execute("""
        SELECT player_tag, player_name, day_number, in_roster
        FROM cwl_performance
        WHERE season_id = ?
        ORDER BY player_tag, day_number
    """, (season_id,))
    rows = cursor.fetchall()
    conn.close()
    
    # Build roster history
    roster_history = {}
    for row in rows:
        tag = row['player_tag']
        if tag not in roster_history:
            roster_history[tag] = {
                'name': row['player_name'],
                'days': {}
            }
        roster_history[tag]['days'][row['day_number']] = row['in_roster'] == 1
    
    return roster_history

def cleanup_old_cwl_data(days: int = 45):
    """Remove CWL data older than specified days"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get old season IDs
    cursor.execute("""
        SELECT season_id FROM cwl_seasons
        WHERE updated_at < datetime('now', ?)
    """, (f'-{days} days',))
    old_seasons = [row['season_id'] for row in cursor.fetchall()]
    
    for season_id in old_seasons:
        cursor.execute("DELETE FROM cwl_performance WHERE season_id = ?", (season_id,))
        cursor.execute("DELETE FROM cwl_standings WHERE season_id = ?", (season_id,))
        cursor.execute("DELETE FROM cwl_bonus_sent WHERE season_id = ?", (season_id,))
        cursor.execute("DELETE FROM cwl_seasons WHERE season_id = ?", (season_id,))

    conn.commit()
    conn.close()
    return len(old_seasons)

# Chat history (AI conversational mode)

def record_chat_message(user_id: str, channel_id: str, role: str, content: str):
    """Persist a chat turn for the AI conversational fallback."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_history (user_id, channel_id, role, content)
        VALUES (?, ?, ?, ?)
    """, (user_id, channel_id, role, content))
    conn.commit()
    conn.close()

def get_chat_history(user_id: str, limit: int = 10) -> list[dict]:
    """Return the most recent chat turns for a user, oldest-first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content, created_at
        FROM chat_history
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    rows.reverse()
    return rows

def cleanup_old_chat_history(days: int = 14, max_per_user: int = 50):
    """Trim chat history: drop rows older than N days, then cap per-user rows."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM chat_history
        WHERE created_at < datetime('now', ?)
    """, (f'-{days} days',))

    cursor.execute("SELECT DISTINCT user_id FROM chat_history")
    user_ids = [row['user_id'] for row in cursor.fetchall()]
    for uid in user_ids:
        cursor.execute("""
            DELETE FROM chat_history
            WHERE id IN (
                SELECT id FROM chat_history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT -1 OFFSET ?
            )
        """, (uid, max_per_user))

    conn.commit()
    conn.close()

# Store Monitor

def get_store_event(event_key: str) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM store_seen_events WHERE event_key = ?", (event_key,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_store_event(event_key: str, title: str, progress_percent: int,
                       claim_ends_at: Optional[int],
                       announced_start: bool, announced_complete: bool):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO store_seen_events
            (event_key, title, progress_percent, claim_ends_at, announced_start, announced_complete, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(event_key) DO UPDATE SET
            title = excluded.title,
            progress_percent = excluded.progress_percent,
            claim_ends_at = excluded.claim_ends_at,
            announced_start = excluded.announced_start,
            announced_complete = excluded.announced_complete,
            updated_at = CURRENT_TIMESTAMP
    """, (event_key, title, progress_percent, claim_ends_at,
          1 if announced_start else 0, 1 if announced_complete else 0))
    conn.commit()
    conn.close()

def has_store_offer_been_announced(offer_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM store_seen_offers WHERE offer_id = ? AND announced = 1", (offer_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def mark_store_offer_announced(offer_id: str, title: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO store_seen_offers (offer_id, title, announced)
        VALUES (?, ?, 1)
        ON CONFLICT(offer_id) DO UPDATE SET announced = 1
    """, (offer_id, title))
    conn.commit()
    conn.close()
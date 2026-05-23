#!/usr/bin/env python3
"""
Write cookies from /cookies/tl_cookie.json into the Chromium profile's
SQLite cookie database before Chromium starts.

Schema must exactly match Chromium 146's expected schema (version 24).
Mismatched schema causes Chromium to wipe the DB on startup.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

COOKIE_FILE = Path("/cookies/tl_cookie.json")
PROFILE_DIR = Path("/profile/Default")
COOKIE_DB   = PROFILE_DIR / "Cookies"

# Chrome timestamps: microseconds since 1601-01-01 00:00:00 UTC
_CHROME_EPOCH_OFFSET_US = 11644473600 * 1_000_000

def to_chrome_time(unix_ts: float) -> int:
    return int(unix_ts * 1_000_000) + _CHROME_EPOCH_OFFSET_US

def main():
    if not COOKIE_FILE.exists():
        print(f"[setup_cookies] ERROR: {COOKIE_FILE} not found", flush=True)
        sys.exit(1)

    data = json.loads(COOKIE_FILE.read_text())
    cookies = [item for item in data if list(item.keys()) != ["user_agent"]]
    if not cookies:
        print("[setup_cookies] ERROR: no cookies in JSON", flush=True)
        sys.exit(1)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Remove any pre-existing DB so Chromium doesn't try to migrate a bad schema
    COOKIE_DB.unlink(missing_ok=True)
    Path(str(COOKIE_DB) + "-journal").unlink(missing_ok=True)
    Path(str(COOKIE_DB) + "-wal").unlink(missing_ok=True)
    Path(str(COOKIE_DB) + "-shm").unlink(missing_ok=True)

    conn = sqlite3.connect(str(COOKIE_DB))
    cur = conn.cursor()

    # Schema must exactly match Chromium 146 (version 24).
    # Chromium wipes the DB on startup if the schema doesn't match.
    cur.executescript("""
        CREATE TABLE meta (
            key   LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY,
            value LONGVARCHAR
        );

        CREATE TABLE cookies (
            creation_utc          INTEGER NOT NULL,
            host_key              TEXT    NOT NULL,
            top_frame_site_key    TEXT    NOT NULL,
            name                  TEXT    NOT NULL,
            value                 TEXT    NOT NULL,
            encrypted_value       BLOB    NOT NULL,
            path                  TEXT    NOT NULL,
            expires_utc           INTEGER NOT NULL,
            is_secure             INTEGER NOT NULL,
            is_httponly           INTEGER NOT NULL,
            last_access_utc       INTEGER NOT NULL,
            has_expires           INTEGER NOT NULL,
            is_persistent         INTEGER NOT NULL,
            priority              INTEGER NOT NULL,
            samesite              INTEGER NOT NULL,
            source_scheme         INTEGER NOT NULL,
            source_port           INTEGER NOT NULL,
            last_update_utc       INTEGER NOT NULL,
            source_type           INTEGER NOT NULL,
            has_cross_site_ancestor INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX cookies_unique_index
            ON cookies(host_key, top_frame_site_key, has_cross_site_ancestor,
                       name, path, source_scheme, source_port);
    """)

    cur.execute("INSERT INTO meta VALUES ('version', '24')")
    cur.execute("INSERT INTO meta VALUES ('last_compatible_version', '24')")
    cur.execute("INSERT INTO meta VALUES ('mmap_status', '-1')")

    now_us     = to_chrome_time(time.time())
    far_future = to_chrome_time(time.time() + 5 * 365 * 86400)

    for i, c in enumerate(cookies):
        domain = c.get("domain", ".torrentleech.org")
        # Only force-add dot prefix for bare second-level domains (e.g. "torrentleech.org").
        # Host-only cookies like "www.torrentleech.org" must NOT get a dot prefix.
        if not domain.startswith(".") and not domain.startswith("www."):
            domain = "." + domain

        expires_raw = c.get("expiry") or c.get("expires")
        expires_utc = to_chrome_time(float(expires_raw)) if expires_raw else far_future
        has_expires = 1 if expires_raw else 0

        try:
            cur.execute("""
                INSERT OR REPLACE INTO cookies
                    (creation_utc, host_key, top_frame_site_key,
                     name, value, encrypted_value,
                     path, expires_utc, is_secure, is_httponly,
                     last_access_utc, has_expires, is_persistent,
                     priority, samesite, source_scheme, source_port,
                     last_update_utc, source_type, has_cross_site_ancestor)
                VALUES (?, ?, '', ?, ?, '', ?, ?, ?, ?, ?, ?, 1, 1, -1, 2, 443, ?, 0, 0)
            """, (
                now_us + i,
                domain,
                c.get("name", ""),
                c.get("value", ""),
                c.get("path", "/"),
                expires_utc,
                1 if c.get("secure", True)   else 0,
                1 if c.get("httpOnly", False) else 0,
                now_us,
                has_expires,
                now_us + i,
            ))
            print(f"  + {c.get('name')} @ {domain}", flush=True)
        except Exception as e:
            print(f"  ! {c.get('name')}: {e}", flush=True)

    conn.commit()
    conn.close()
    print(f"[setup_cookies] Done — {len(cookies)} cookies written to {COOKIE_DB}", flush=True)

if __name__ == "__main__":
    main()

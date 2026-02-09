#!/usr/bin/env python3
"""
qBittorrent Category Size Checker
Calculates the total size of all torrents in a specific category.
"""
import os
from dotenv import load_dotenv
from qbittorrentapi import Client, APIConnectionError

# --- Configuration ---
TARGET_CATEGORY = "1_year_torrents"

# Load .env file from the PARENT directory
# (This logic matches your working script)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(BASE_DIR, '.env')
load_dotenv(dotenv_path)

def format_bytes(byte_count):
    """Converts bytes into a human-readable format."""
    if byte_count is None or byte_count < 0:
        return "N/A"
    if byte_count == 0:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: ' B', 1: ' KB', 2: ' MB', 3: ' GB', 4: ' TB'}
    # Fix: Ensure n doesn't go out of bounds
    while byte_count >= power and n < (len(power_labels) - 1):
        byte_count /= power
        n += 1
    return f"{byte_count:.2f}{power_labels[n]}"

def check_size():
    qbt_client = None
    
    # Get credentials from environment
    qbit_url = os.getenv("QBIT_URL")
    qbit_user = os.getenv("QBIT_USER")
    qbit_pass = os.getenv("QBIT_PASS")

    # Check if credentials loaded successfully
    if not all([qbit_url, qbit_user, qbit_pass]):
        print(f"Error: Missing QBIT_URL, QBIT_USER, or QBIT_PASS.")
        print(f"Attempted to load .env file from: {dotenv_path}")
        print("Please ensure the .env file exists in that location and contains the required variables.")
        return

    try:
        print(f"Connecting to qBittorrent at {qbit_url} to check category: '{TARGET_CATEGORY}'")
        qbt_client = Client(
            host=qbit_url,
            username=qbit_user,
            password=qbit_pass
        )
        qbt_client.auth_log_in()

        total_size_bytes = 0
        torrent_count = 0

        # Get all torrents and filter them by category
        torrents_in_category = qbt_client.torrents_info(category=TARGET_CATEGORY)
        
        for torrent in torrents_in_category:
            total_size_bytes += torrent.size
            torrent_count += 1
            
        print("-" * 30)
        print(f"Found {torrent_count} torrents in the category.")
        print(f"Total size: {format_bytes(total_size_bytes)}")
        print("-" * 30)

    except APIConnectionError as e:
        print(f"Error: Could not connect to qBittorrent. Please check your .env settings. Details: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if qbt_client and qbt_client.is_logged_in:
            qbt_client.auth_log_out()
            print("Disconnected from qBittorrent.")

if __name__ == "__main__":
    check_size()
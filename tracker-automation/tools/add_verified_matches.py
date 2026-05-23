#!/usr/bin/env python3

"""
Helper script to add manually verified matches to the ML training dataset.
Paste your Discord-format matches and it will convert & append to verified_matches.json

Format: 
Tracker Name (size unit) == qBit Name (size unit) #optional notes
"""

import os
import sys
import json
import re
from datetime import datetime

# --- PATH FIX ---
scripts_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(scripts_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR_JSON = os.path.join(BASE_DIR, 'storage', 'json')
VERIFIED_MATCHES_FILE = os.path.join(STORAGE_DIR_JSON, 'verified_matches.json')

def parse_size_to_mb(size_str):
    """
    Parse size string to megabytes.
    Supports: MB, MiB, GB, GiB, TB, TiB, KB, KiB
    """
    size_str = size_str.strip().upper()
    
    # Extract number and unit using a better regex
    match = re.match(r'([\d.,]+)\s*([A-Z]+)?', size_str)
    if not match:
        print(f"⚠ Warning: Could not parse size '{size_str}', defaulting to 0")
        return 0.0
    
    # Remove commas from number (e.g., "1,234.56")
    value_str = match.group(1).replace(',', '')
    value = float(value_str)
    unit = match.group(2) if match.group(2) else 'MB'
    
    # Convert to MB based on unit
    if unit in ['TB', 'TIB']:
        return value * 1024 * 1024  # TB/TiB to MB
    elif unit in ['GB', 'GIB']:
        return value * 1024  # GB/GiB to MB
    elif unit in ['MB', 'MIB']:
        return value  # Already in MB
    elif unit in ['KB', 'KIB']:
        return value / 1024  # KB/KiB to MB
    elif unit == 'B':
        return value / (1024 * 1024)  # Bytes to MB
    else:
        print(f"⚠ Warning: Unknown unit '{unit}' in '{size_str}', assuming MB")
        return value

def parse_match_line(line):
    """
    Parse a line in format:
    WITH SIZES: Tracker Name (size unit) == qBit Name (size unit) #optional notes
    WITHOUT SIZES: Tracker Name == qBit Name #optional notes
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Split on ==
    if '==' not in line:
        print(f"⚠ Skipping invalid line (no ==): {line[:50]}...")
        return None
    
    parts = line.split('==', 1)
    tracker_part = parts[0].strip()
    qbit_part = parts[1].strip()
    
    # Extract notes if present (after #)
    notes = ""
    if '#' in qbit_part:
        qbit_part, notes = qbit_part.split('#', 1)
        qbit_part = qbit_part.strip()
        notes = notes.strip()
    
    # Try to parse WITH sizes first: "Name (size unit)"
    tracker_match = re.match(r'(.+?)\s*\(([^)]+)\)\s*$', tracker_part)
    if tracker_match:
        # Has size info
        tracker_name = tracker_match.group(1).strip()
        tracker_size_str = tracker_match.group(2).strip()
        tracker_size_mb = parse_size_to_mb(tracker_size_str)
    else:
        # No size info - just the name
        tracker_name = tracker_part.strip()
        tracker_size_mb = 0.0
    
    # Try to parse qBit WITH sizes: "Name (size unit)"
    qbit_match = re.match(r'(.+?)\s*\(([^)]+)\)\s*$', qbit_part)
    if qbit_match:
        # Has size info
        qbit_name = qbit_match.group(1).strip()
        qbit_size_str = qbit_match.group(2).strip()
        qbit_size_mb = parse_size_to_mb(qbit_size_str)
    else:
        # No size info - just the name
        qbit_name = qbit_part.strip()
        qbit_size_mb = 0.0
    
    return {
        'tracker_name': tracker_name,
        'tracker_size_mb': round(tracker_size_mb, 2),
        'qbit_name': qbit_name,
        'qbit_size_mb': round(qbit_size_mb, 2),
        'verified_by': 'manual',
        'date': datetime.now().strftime('%Y-%m-%d'),
        'notes': notes
    }

def load_verified_matches():
    """Load existing verified matches or create new structure"""
    if os.path.exists(VERIFIED_MATCHES_FILE):
        try:
            with open(VERIFIED_MATCHES_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("⚠ Warning: Existing file corrupted, creating new one")
    
    return {
        'last_updated': datetime.now().isoformat(),
        'total_verified_matches': 0,
        'matches': []
    }

def save_verified_matches(data):
    """Save verified matches to JSON file"""
    data['last_updated'] = datetime.now().isoformat()
    data['total_verified_matches'] = len(data['matches'])
    
    os.makedirs(STORAGE_DIR_JSON, exist_ok=True)
    
    with open(VERIFIED_MATCHES_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✅ Saved {data['total_verified_matches']} verified matches to:")
    print(f"   {VERIFIED_MATCHES_FILE}")

def main():
    print("="*80)
    print("VERIFIED MATCHES IMPORTER")
    print("="*80)
    print("\nPaste your Discord-format matches below (one per line).")
    print("Format: Tracker Name (size) == qBit Name (size) #notes")
    print("\nPress Ctrl+D (Linux/Mac) or Ctrl+Z then Enter (Windows) when done.")
    print("Or type 'DONE' on a new line.\n")
    print("-"*80)
    
    # Read input
    lines = []
    try:
        while True:
            line = input()
            if line.strip().upper() == 'DONE':
                break
            lines.append(line)
    except EOFError:
        pass
    
    if not lines:
        print("\n⚠ No input provided. Exiting.")
        return
    
    print("\n" + "="*80)
    print(f"Processing {len(lines)} lines...")
    print("="*80 + "\n")
    
    # Parse all lines
    parsed_matches = []
    for i, line in enumerate(lines, 1):
        match_data = parse_match_line(line)
        if match_data:
            parsed_matches.append(match_data)
            print(f"✓ Line {i}: {match_data['tracker_name'][:50]}...")
        else:
            if line.strip() and not line.strip().startswith('#'):
                print(f"✗ Line {i}: Failed to parse")
    
    if not parsed_matches:
        print("\n⚠ No valid matches parsed. Exiting.")
        return
    
    # Load existing data and append
    data = load_verified_matches()
    
    print(f"\n📊 Current database: {data['total_verified_matches']} matches")
    print(f"📊 Adding: {len(parsed_matches)} new matches")
    
    data['matches'].extend(parsed_matches)
    
    # Save
    save_verified_matches(data)
    
    print(f"\n🎉 Successfully added {len(parsed_matches)} verified matches!")
    print(f"📊 Total in database: {data['total_verified_matches']} matches")

if __name__ == "__main__":
    main()


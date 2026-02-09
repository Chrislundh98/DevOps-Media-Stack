#!/usr/bin/env python3
"""
Bazarr Extended Edition Subtitle Fixer
Automatically finds Extended movies, removes wrong subs, downloads correct Extended subs.
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

# Paths
ENV_PATH = "/volume1/automation/.env"
MOVIE_SCAN_PATH = "/volume2/media/movies"
CACHE_FILE = "/volume1/automation/storage/json/bazarr_extended_processed.json"

# Load environment
load_dotenv(ENV_PATH)
BAZARR_URL = os.getenv("BAZARR_URL", "").rstrip('/')
BAZARR_API = os.getenv("BAZARR_API")

# Settings
LANGUAGE = "en"
MIN_SCORE = 70


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"processed": []}
    return {"processed": []}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def api_get(endpoint, params=None, timeout=180):
    """GET request to Bazarr API"""
    try:
        r = requests.get(
            f"{BAZARR_URL}/api{endpoint}",
            headers={"X-API-KEY": BAZARR_API},
            params=params,
            timeout=timeout
        )
        if r.status_code == 200:
            return r.json()
        else:
            print(f"    API returned {r.status_code}: {r.text[:200] if r.text else 'empty'}")
    except requests.exceptions.Timeout:
        print(f"    API timeout after {timeout}s")
    except Exception as e:
        print(f"    API Error: {e}")
    return None


def api_delete(endpoint, params):
    """DELETE request to Bazarr API"""
    try:
        r = requests.delete(
            f"{BAZARR_URL}/api{endpoint}",
            headers={"X-API-KEY": BAZARR_API},
            params=params,
            timeout=30
        )
        return r.status_code in [200, 204]
    except:
        return False


def api_patch(endpoint, params, json_data):
    """PATCH request to Bazarr API"""
    try:
        url = f"{BAZARR_URL}/api{endpoint}"
        r = requests.patch(
            url,
            headers={"X-API-KEY": BAZARR_API},
            params=params,
            json=json_data,
            timeout=300  # 5 minutes - downloads can take a while
        )
        if r.status_code in [200, 201, 204]:
            return True
        else:
            print(f"    API Error: {r.status_code} - {r.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print(f"    Download timed out (5 min) - may still be processing")
        return False
    except Exception as e:
        print(f"    Error: {e}")
        return False


def scan_for_extended_movies():
    """Scan movie library for Extended editions"""
    print(f"Scanning {MOVIE_SCAN_PATH} for Extended editions...")
    extended_files = []
    video_extensions = ('.mkv', '.mp4', '.avi', '.m4v')
    
    for folder in os.listdir(MOVIE_SCAN_PATH):
        folder_path = os.path.join(MOVIE_SCAN_PATH, folder)
        if not os.path.isdir(folder_path):
            continue
        
        for file in os.listdir(folder_path):
            if file.endswith(video_extensions) and 'extended' in file.lower():
                extended_files.append({
                    'filename': file,
                    'path': os.path.join(folder_path, file),
                    'folder': folder
                })
    
    print(f"Found {len(extended_files)} Extended edition(s)")
    return extended_files


def get_bazarr_movies():
    """Fetch all movies from Bazarr"""
    print("Fetching movies from Bazarr...")
    all_movies = []
    start = 0
    
    while True:
        result = api_get("/movies", {"start": start, "length": 500})
        if not result or 'data' not in result:
            break
        
        movies = result['data']
        if not movies:
            break
        
        all_movies.extend(movies)
        if len(movies) < 500:
            break
        start += 500
    
    print(f"Fetched {len(all_movies)} movies from Bazarr")
    return all_movies


def find_movie_in_bazarr(filename, bazarr_movies):
    """Match local file to Bazarr movie"""
    for movie in bazarr_movies:
        bazarr_filename = os.path.basename(movie.get('path', ''))
        if bazarr_filename == filename:
            return movie
    return None


def delete_existing_subtitles(movie):
    """Delete all existing subtitles for a movie"""
    radarr_id = movie['radarrId']
    subtitles = movie.get('subtitles', [])
    
    if not subtitles:
        print("    No existing subtitles")
        return
    
    print(f"    Deleting {len(subtitles)} existing subtitle(s)...")
    for sub in subtitles:
        sub_path = sub.get('path', '')
        sub_lang = sub.get('code2', '') or sub.get('language', '')
        if sub_path:
            api_delete("/movies/subtitles", {
                "radarrId": radarr_id,
                "path": sub_path,
                "language": sub_lang
            })
    time.sleep(1)


def search_and_download_extended_subtitle(movie):
    """Search for Extended subtitles and download the best one"""
    radarr_id = movie['radarrId']
    
    print("    Searching subtitle providers...")
    result = api_get("/providers/movies", {"radarrid": radarr_id})
    
    print(f"    Raw result type: {type(result)}, content: {str(result)[:200] if result else 'None'}")
    
    if not result:
        print("    No results from providers")
        return False
    
    # Handle different response formats
    subtitles = []
    if isinstance(result, list):
        subtitles = result
    elif isinstance(result, dict):
        subtitles = result.get('data', []) or result.get('subtitles', [])
    
    if not subtitles:
        print("    No subtitles found")
        return False
    
    print(f"    Found {len(subtitles)} total subtitles")
    
    # Filter for Extended subtitles in the right language
    extended_subs = []
    for sub in subtitles:
        # release_info is a LIST, not a string
        release_info = sub.get('release_info', [])
        if isinstance(release_info, list):
            release = release_info[0] if release_info else ''
        else:
            release = str(release_info)
        
        # Must contain "Extended" (case insensitive)
        if not release or 'extended' not in release.lower():
            continue
        
        # Check language
        sub_lang = sub.get('language', '').lower()
        if sub_lang not in ['en', 'eng', 'english']:
            continue
        
        score = sub.get('score', 0)
        if score >= MIN_SCORE:
            extended_subs.append((score, release, sub))
    
    if not extended_subs:
        print("    No Extended subtitles found matching criteria")
        return False
    
    # Sort by score, get best
    extended_subs.sort(reverse=True, key=lambda x: x[0])
    best_score, best_release, best_sub = extended_subs[0]
    
    print(f"    Best match: {best_release[:60]}... (Score: {best_score})")
    
    # Download it - radarrid goes in the JSON body, not query params
    success = api_patch("/movies/subtitles", {}, {
        "radarrid": radarr_id,
        "provider": best_sub.get('provider'),
        "subtitle": best_sub.get('subtitle'),
        "language": best_sub.get('language') or LANGUAGE,
        "hi": best_sub.get('hearing_impaired', 'False'),
        "forced": best_sub.get('forced', 'False')
    })
    
    if success:
        print("    ✓ Downloaded Extended subtitle")
        return True
    else:
        print("    ✗ Download failed")
        return False


def main():
    if not BAZARR_URL or not BAZARR_API:
        print("ERROR: BAZARR_URL or BAZARR_API not set in .env")
        sys.exit(1)
    
    print("=" * 60)
    print("Bazarr Extended Edition Subtitle Fixer")
    print("=" * 60)
    
    # Load cache
    cache = load_cache()
    processed = set(cache.get('processed', []))
    
    # Scan for Extended movies
    extended_files = scan_for_extended_movies()
    if not extended_files:
        print("No Extended editions found")
        return
    
    # Filter out already processed
    to_process = [f for f in extended_files if f['filename'] not in processed]
    print(f"{len(to_process)} new Extended edition(s) to process")
    
    if not to_process:
        print("All Extended editions already processed")
        return
    
    # Get Bazarr movies
    bazarr_movies = get_bazarr_movies()
    if not bazarr_movies:
        print("ERROR: Could not fetch movies from Bazarr")
        return
    
    # Process each Extended movie
    success = 0
    failed = 0
    
    for ext_file in to_process:
        print(f"\n{'='*60}")
        print(f"Processing: {ext_file['folder']}")
        print(f"File: {ext_file['filename']}")
        
        # Find in Bazarr
        movie = find_movie_in_bazarr(ext_file['filename'], bazarr_movies)
        if not movie:
            print("    SKIP: Not found in Bazarr")
            failed += 1
            continue
        
        print(f"    Radarr ID: {movie['radarrId']}")
        
        # Delete existing subs
        delete_existing_subtitles(movie)
        
        # Search and download Extended subtitle
        if search_and_download_extended_subtitle(movie):
            success += 1
            processed.add(ext_file['filename'])
            cache['processed'] = list(processed)
            save_cache(cache)
        else:
            failed += 1
        
        time.sleep(5)  # Give Bazarr time to settle between movies
    
    # Summary
    print(f"\n{'='*60}")
    print("DONE")
    print(f"Success: {success}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
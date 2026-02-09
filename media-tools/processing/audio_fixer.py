#!/usr/bin/env python3
import os
import subprocess
import logging
from pathlib import Path

MEDIA_PATH = "/volume2/media"

base_dir = Path(__file__).parent.parent.parent
log_dir = base_dir / 'logs' / 'media'
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'audio_fixer.log'),
        logging.StreamHandler()
    ]
)

def check_audio_tracks(mkv_file):
    try:
        result = subprocess.run(
            ['mkvmerge', '-J', str(mkv_file)],
            capture_output=True,
            text=True,
            check=True
        )
        
        import json
        info = json.loads(result.stdout)
        audio_tracks = [t for t in info.get('tracks', []) if t['type'] == 'audio']
        
        return len(audio_tracks)
        
    except Exception as e:
        logging.error(f"Failed to check {mkv_file}: {e}")
        return 0

def find_mkv_files():
    mkvs = []
    for root, _, files in os.walk(MEDIA_PATH):
        for file in files:
            if file.endswith('.mkv'):
                mkvs.append(Path(root) / file)
    return mkvs

def main():
    logging.info("Audio Fixer - Starting")
    
    mkvs = find_mkv_files()
    logging.info(f"Scanning {len(mkvs)} MKV files")
    
    issues = []
    for mkv in mkvs:
        track_count = check_audio_tracks(mkv)
        if track_count == 0:
            issues.append(mkv)
            logging.warning(f"No audio tracks: {mkv}")
    
    logging.info(f"Found {len(issues)} files with audio issues")

if __name__ == "__main__":
    main()

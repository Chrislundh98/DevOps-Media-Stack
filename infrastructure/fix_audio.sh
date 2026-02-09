#!/bin/bash

# --- Configuration ---
MEDIA_DIRECTORY="/volume2/media"
CACHE_DIR="/volume1/automation/storage/txt"
CACHE_FILE="$CACHE_DIR/mkv_audio_cache.txt"

# --- Setup ---
mkdir -p "$CACHE_DIR"
touch "$CACHE_FILE"

echo "Starting scan: $(date)"
echo "Scanning for MP4 files in: $MEDIA_DIRECTORY"
echo ""

echo "Counting total MP4 files..."
total_files=$(find "$MEDIA_DIRECTORY" -type f -name "*.mp4" | wc -l)
echo "Total MP4 files found: $total_files"
echo ""

if [ "$total_files" -eq 0 ]; then
    echo "ERROR: No MP4 files found! Check your path."
    exit 1
fi

declare -A cache
while IFS='|' read -r filepath modtime; do
    cache["$filepath"]="$modtime"
done < "$CACHE_FILE"

echo "Loaded cache with $(wc -l < "$CACHE_FILE") entries"
echo "Starting processing..."
echo ""

# Temporary file for new cache
temp_cache=$(mktemp)

processed=0
skipped=0
fixed=0
count=0

while IFS= read -r -d $'\0' file; do
    
    count=$((count + 1))
    
    current_modtime=$(stat -c %Y "$file" 2>/dev/null || stat -f %m "$file" 2>/dev/null)
    
    if [[ -n "${cache[$file]}" ]] && [[ "${cache[$file]}" == "$current_modtime" ]]; then
        echo "$file|$current_modtime" >> "$temp_cache"
        skipped=$((skipped + 1))
        
        if [ $((skipped % 100)) -eq 0 ]; then
            echo "[$count/$total_files] [CACHED] $skipped files skipped so far..."
        fi
        continue
    fi
    
    echo "[$count/$total_files] [CHECKING] $(basename "$file")"
    
    container_path=$(echo "$file" | sed 's|^/volume2/media|/media|')
    json_data=$(docker exec mkvtoolnix mkvmerge -J "$container_path")

    if [ -z "$json_data" ] || [[ "$json_data" == *"Error:"* ]]; then
        echo "  [ERROR] Could not read file metadata"
        continue
    fi

    english_track_id=$(echo "$json_data" | jq -r '(.tracks // [])[] | select(.type == "audio" and .properties.language == "eng") | .id' | head -n1)

    default_track_info=$(echo "$json_data" | jq -r '(.tracks // [])[] | select(.type == "audio" and .properties.default_track == true) | "\(.id) \(.properties.language)"' | head -n1)
    
    default_track_id=""
    default_track_lang=""

    if [ -n "$default_track_info" ]; then
        default_track_id=$(echo "$default_track_info" | cut -d' ' -f1 | tr -d '\n\r')
        default_track_lang=$(echo "$default_track_info" | cut -d' ' -f2 | tr -d '\n\r')
    fi

    if [ -n "$default_track_id" ] && [ -n "$english_track_id" ]; then
        if [ "$default_track_lang" != "swe" ] && [ "$default_track_lang" != "eng" ]; then

            default_track_id=$(echo "$default_track_id" | tr -cd '0-9')
            english_track_id=$(echo "$english_track_id" | tr -cd '0-9')
            
            default_track_num=$((default_track_id + 1))
            english_track_num=$((english_track_id + 1))
            
            echo "  [FIXING] Setting track #${english_track_num} (ENG) as default (was: $default_track_lang)"

            docker exec mkvtoolnix mkvpropedit "$container_path" \
                --edit track:${english_track_num} --set flag-default=1 \
                --edit track:${default_track_num} --set flag-default=0
            
            fixed=$((fixed + 1))
        else
            echo "  [OK] Already correct (default: $default_track_lang)"
        fi
    else
        echo "  [OK] No changes needed"
    fi
    
    echo "$file|$current_modtime" >> "$temp_cache"
    processed=$((processed + 1))

done < <(find "$MEDIA_DIRECTORY" -type f -name "*.mp4" -print0)

mv "$temp_cache" "$CACHE_FILE"

echo ""
echo "=========================================="
echo "Scan completed: $(date)"
echo "Total MP4 files discovered: $total_files"
echo "Files actually processed: $count"
echo "Files checked: $processed"
echo "Files skipped (cached): $skipped"
echo "Files fixed: $fixed"
echo "Cache entries saved: $(wc -l < "$CACHE_FILE")"
echo "Cache stored at: $CACHE_FILE"
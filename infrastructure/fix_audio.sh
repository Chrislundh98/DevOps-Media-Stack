#!/bin/bash
# Scan the media library and ensure English audio is the default track on
# every MP4. Caches per-file mtime so subsequent runs only re-check changed
# files. Uses mkvtoolnix in a Docker container to inspect/modify tracks.

set -u

MEDIA_DIRECTORY="${MEDIA_DIRECTORY:-/mnt/storage/media}"
CACHE_DIR="${CACHE_DIR:-./cache}"
CACHE_FILE="$CACHE_DIR/mkv_audio_cache.txt"
MKV_CONTAINER="${MKV_CONTAINER:-mkvtoolnix}"
HOST_MEDIA_PREFIX="${HOST_MEDIA_PREFIX:-/mnt/storage/media}"
CONTAINER_MEDIA_PREFIX="${CONTAINER_MEDIA_PREFIX:-/media}"

mkdir -p "$CACHE_DIR"
touch "$CACHE_FILE"

echo "Scan started: $(date)"
echo "Target directory: $MEDIA_DIRECTORY"

total_files=$(find "$MEDIA_DIRECTORY" -type f -name "*.mp4" | wc -l)
echo "Total MP4 files: $total_files"

if [ "$total_files" -eq 0 ]; then
    echo "ERROR: No MP4 files found."
    exit 1
fi

declare -A cache
while IFS='|' read -r filepath modtime; do
    cache["$filepath"]="$modtime"
done < "$CACHE_FILE"

echo "Loaded cache: $(wc -l < "$CACHE_FILE") entries"

temp_cache=$(mktemp)
processed=0
skipped=0
fixed=0
count=0

while IFS= read -r -d $'\0' file; do
    count=$((count + 1))
    current_modtime=$(stat -c %Y "$file" 2>/dev/null || stat -f %m "$file" 2>/dev/null)

    if [[ -n "${cache[$file]:-}" && "${cache[$file]}" == "$current_modtime" ]]; then
        echo "$file|$current_modtime" >> "$temp_cache"
        skipped=$((skipped + 1))
        [ $((skipped % 100)) -eq 0 ] && echo "[$count/$total_files] cached ($skipped skipped)"
        continue
    fi

    echo "[$count/$total_files] checking $(basename "$file")"

    container_path=${file/$HOST_MEDIA_PREFIX/$CONTAINER_MEDIA_PREFIX}
    json_data=$(docker exec "$MKV_CONTAINER" mkvmerge -J "$container_path")

    if [ -z "$json_data" ] || [[ "$json_data" == *"Error:"* ]]; then
        echo "  [error] could not read metadata"
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
            # mkvpropedit track indices are 1-based
            default_track_id=$(echo "$default_track_id" | tr -cd '0-9')
            english_track_id=$(echo "$english_track_id" | tr -cd '0-9')
            default_track_num=$((default_track_id + 1))
            english_track_num=$((english_track_id + 1))

            echo "  [fixing] setting track #$english_track_num (eng) as default (was: $default_track_lang)"

            docker exec "$MKV_CONTAINER" mkvpropedit "$container_path" \
                --edit track:${english_track_num} --set flag-default=1 \
                --edit track:${default_track_num} --set flag-default=0

            fixed=$((fixed + 1))
        else
            echo "  [ok] already correct ($default_track_lang)"
        fi
    else
        echo "  [ok] no changes needed"
    fi

    echo "$file|$current_modtime" >> "$temp_cache"
    processed=$((processed + 1))
done < <(find "$MEDIA_DIRECTORY" -type f -name "*.mp4" -print0)

mv "$temp_cache" "$CACHE_FILE"

echo
echo "Scan completed: $(date)"
echo "Discovered: $total_files | processed: $processed | skipped: $skipped | fixed: $fixed"

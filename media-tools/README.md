# Media Tools

Small standalone scripts that plug into the rest of the media stack: MakeMKV / mkvtoolnix in Docker, Bazarr's HTTP API, and a generic RAR extractor for downloaded archives.

## ripping/

| Script | Purpose |
| --- | --- |
| `makemkv.py` | Walks the movies tree for `*.iso`, calls into the MakeMKV container (`docker exec ... makemkvcon`), and queues each disc for ripping. |
| `auto_extract.py` | Finds `.rar` archives under `DOWNLOAD_PATH`, extracts them to `EXTRACT_PATH`, and cleans up the original parts on success. |

## processing/

| Script | Purpose |
| --- | --- |
| `audio_fixer.py` | Scans the MKV library and reports/fixes files with missing or misconfigured audio tracks. Uses `mkvmerge -J` for inspection. |
| `bazarr_fixer.py` | Finds movies whose release name contains "Extended" but whose existing subtitles are for the theatrical cut, deletes the bad subs, and asks Bazarr to re-search. Cache file prevents re-processing. |

All paths and credentials come from environment variables (`MEDIA_PATH`, `MOVIES_PATH`, `DOWNLOAD_PATH`, `BAZARR_URL`, `BAZARR_API`, ...). See each script's top section for the variables it reads.

# Interview Transcriber

Structured interview transcription powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper large-v3). Automatically detects CUDA for GPU acceleration with CPU fallback. Produces clean, organized Q&A formatted transcripts with timestamps and optional interview guide matching.

Built for processing one-on-one interviews where clean, readable output matters.

## Features

- **Zero-config batch processing** — run with no arguments to process all audio files in the current directory
- **Automatic device detection** — uses CUDA when available, falls back to CPU gracefully
- **Clean sentence output** — VAD filtering + segment merging eliminates fragmented text
- **Speaker turn detection** — identifies interviewer vs. respondent via pause analysis
- **Interview guide matching** — optionally maps detected questions to your prepared guide
- **Multiple output formats** — DOCX, TXT, JSON

## Prerequisites

- Python 3.10+
- NVIDIA GPU with CUDA support (optional but recommended)

## Installation

```bash
git clone https://github.com/ChristofferNL/interview-transcriber.git
cd interview-transcriber
pip install -r requirements.txt
```

### GPU Acceleration (Optional)

For CUDA support, install PyTorch with the appropriate CUDA version for your GPU:

```bash
# Standard GPUs (RTX 3000/4000 series)
pip install torch --index-url https://download.pytorch.org/whl/cu124

# RTX 5000 series (Blackwell, sm_120) — requires nightly build
pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128
```

Verify CUDA is working:
```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

> **Note:** The first run downloads the Whisper large-v3 model (~3 GB). This is cached for subsequent runs.

## Quick Start

```bash
# Process all audio files in current directory
python transcribe.py

# Single file
python transcribe.py interview.mp3

# With interview guide
python transcribe.py interview.mp3 --guide questions.txt

# Batch process a specific folder
python transcribe.py ./interviews/

# English interview with smaller model
python transcribe.py --language en --model medium
```

## Usage

```
python transcribe.py [-h] [--guide FILE] [--output PATH] [--format {docx,txt,both,json}]
                     [--model MODEL] [--device {auto,cuda,cpu}] [--language LANG]
                     [--beam-size N] [--pause-threshold SEC]
                     [input]
```

| Flag | Default | Description |
|------|---------|-------------|
| `input` | `.` (current dir) | Audio file or directory |
| `--guide`, `-g` | — | Interview guide file for question matching |
| `--output`, `-o` | auto | Output file path |
| `--format`, `-f` | `docx` | Output format: `docx`, `txt`, `both`, `json` |
| `--model`, `-m` | `large-v3` | Whisper model size |
| `--device` | `auto` | `auto`, `cuda`, or `cpu` |
| `--language`, `-l` | `sv` | ISO 639-1 language code |
| `--beam-size` | `5` | Beam search width (1–10, higher = more accurate) |
| `--pause-threshold` | `2.0` | Seconds of silence to trigger speaker change |

## Configuration

Default values are defined at the top of `transcribe.py`:

```python
MODEL_SIZE      = "large-v3"    # tiny, base, small, medium, large-v2, large-v3
LANGUAGE        = "sv"          # ISO 639-1: sv, en, de, fr, es, etc.
BEAM_SIZE       = 5             # Higher = more accurate, slower (1-10)
PAUSE_THRESHOLD = 2.0           # Seconds of silence to detect speaker change

INITIAL_PROMPT = (
    "Det här är en intervju på svenska. "
    "Intervjuaren ställer frågor och respondenten svarar."
)
```

> When switching languages, update both `LANGUAGE` and `INITIAL_PROMPT`.

## Interview Guide

Optionally provide a text file with your interview questions. The transcriber maps detected questions to guide entries in order. Supported formats:

```
Q1: What is your background?
Q2: How do you approach this topic?
```
```
Fråga 1: Berätta om din bakgrund.
Fråga 2: Hur ser du på ämnet?
```
```
1. First question
2. Second question
```

Or one question per line with no numbering.

## Output Example

```
Q1 [00:15]:
  (Guide: Kan du berätta lite om dig själv?)
Ja, kan du berätta lite om din bakgrund och hur du hamnade här?

A1 [00:22]:
Absolut. Jag har jobbat inom IT-säkerhet i ungefär fem år nu. Jag började
egentligen med nätverksadministration men blev mer och mer intresserad av
säkerhetssidan...

────────────────────────────────────────

Q2 [03:41]:
  (Guide: Vilka utmaningar har du stött på?)
Vilka utmaningar har du stött på i ditt arbete?

A2 [03:48]:
Den största utmaningen är nog att hålla sig uppdaterad...
```

## Tuning

**Speaker detection off?** Adjust `--pause-threshold`:
- Lower (1.0–1.5s) for fast-paced interviews
- Higher (2.5–4.0s) for interviews with long natural pauses within answers

**Audio quality issues?** Try `--beam-size 8` for more aggressive decoding.

**No GPU?** Runs fine on CPU — a 15-minute interview takes roughly 10–15 minutes with automatic `int8` quantization.

## How It Works

1. **VAD Filtering** — Silero VAD pre-filters audio so Whisper only processes actual speech
2. **Transcription** — faster-whisper (CTranslate2) runs beam search with temperature fallbacks
3. **Segment Merging** — fragments are combined into complete sentences using punctuation and gap analysis
4. **Text Cleaning** — removes repeated words, fixes spacing, normalizes capitalization
5. **Speaker Detection** — pause patterns identify interviewer vs. respondent turns
6. **Structuring** — segments are grouped into Q&A blocks with timestamps

## Supported Audio Formats

mp3, wav, m4a, flac, ogg, wma, aac, mp4, mkv, webm

## License

MIT
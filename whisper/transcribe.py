#!/usr/bin/env python3
"""
Interview Transcription System

Structured interview transcription using faster-whisper with automatic
CUDA/CPU detection. Produces clean Q&A formatted output with timestamps,
speaker detection, and optional interview guide matching.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────
# Configuration — Modify these defaults to match your setup.
# All values can also be overridden via CLI flags (--help).
# ──────────────────────────────────────────────────────────────

MODEL_SIZE      = "large-v3"    # tiny, base, small, medium, large-v2, large-v3
LANGUAGE        = "sv"          # ISO 639-1: sv, en, de, fr, es, etc.
BEAM_SIZE       = 5             # Higher = more accurate, slower (1-10) << CHANGE THIS VALUE FOR BETTER RESULT DEPENDENT ON YOUR HARDWARE
PAUSE_THRESHOLD = 2.0           # Seconds of silence to detect speaker change

# Context prompt — adjust for your language/interview style
INITIAL_PROMPT = (
    "Det här är en intervju på svenska. "
    "Intervjuaren ställer frågor och respondenten svarar."
)

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg",
    ".wma", ".aac", ".mp4", ".mkv", ".webm",
}


# ──────────────────────────────────────────────────────────────
# Dependency Check
# ──────────────────────────────────────────────────────────────

def check_dependencies():
    missing = []
    for module, package in [
        ("faster_whisper", "faster-whisper"),
        ("docx", "python-docx"),
        ("rich", "rich"),
    ]:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"Missing: {', '.join(missing)}")
        print(f"Install: pip install {' '.join(missing)}")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────
# Device Detection
# ──────────────────────────────────────────────────────────────

def detect_device(requested: str = "auto") -> tuple[str, str]:
    """
    Returns (device, compute_type). Prefers CUDA, falls back to CPU.
    """
    if requested == "cpu":
        return "cpu", "int8"

    cuda_available = False
    if requested in ("cuda", "auto"):
        try:
            import torch
            cuda_available = torch.cuda.is_available()
            if cuda_available:
                try:
                    gpu_name = torch.cuda.get_device_name(0)
                    props = torch.cuda.get_device_properties(0)
                    vram = props.total_memory / (1024 ** 3)
                    print(f"  GPU detected: {gpu_name} ({vram:.1f} GB)")

                    # Verify the GPU is actually usable by running a small tensor op
                    test = torch.zeros(1, device="cuda")
                    del test
                except Exception as e:
                    print(f"  GPU found but not usable: {e}")
                    cuda_available = False
        except ImportError:
            try:
                import ctranslate2
                cuda_available = "cuda" in ctranslate2.get_supported_compute_types("cuda")
            except Exception:
                pass

    if cuda_available:
        return "cuda", "float16"

    if requested == "cuda":
        print("WARNING: CUDA requested but unavailable — falling back to CPU.")
    elif requested == "auto":
        print("  CUDA unavailable — using CPU (int8)")

    return "cpu", "int8"


# ──────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────

@dataclass
class Config:
    model_size: str = MODEL_SIZE
    device: str = "auto"
    compute_type: str = "auto"
    language: str = LANGUAGE
    beam_size: int = BEAM_SIZE
    best_of: int = 5
    patience: float = 1.0
    vad_filter: bool = True
    vad_parameters: dict = field(default_factory=lambda: {
        "min_silence_duration_ms": 600,
        "speech_pad_ms": 200,
        "threshold": 0.35,
        "min_speech_duration_ms": 250,
    })
    merge_gap_threshold: float = 1.5
    min_segment_words: int = 3
    pause_for_turn_switch: float = PAUSE_THRESHOLD
    guide_path: Optional[str] = None
    output_format: str = "docx"


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


# ──────────────────────────────────────────────────────────────
# Interview Guide Parser
# ──────────────────────────────────────────────────────────────

def parse_interview_guide(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    patterns = [
        r'(?:Q|Fråga|Question|F)\s*(\d+)\s*[:\.]\s*(.+?)(?=(?:Q|Fråga|Question|F)\s*\d+\s*[:\.]|\Z)',
        r'(\d+)\s*[:\.]\s*(.+?)(?=\d+\s*[:\.]|\Z)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
        if matches:
            return [{"number": int(n), "text": t.strip()} for n, t in matches]

    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
    return [
        {"number": i, "text": re.sub(r'^\d+[\.\):\s]+', '', l).strip()}
        for i, l in enumerate(lines, 1) if l.strip()
    ]


# ──────────────────────────────────────────────────────────────
# Transcription Engine
# ──────────────────────────────────────────────────────────────

class Transcriber:
    def __init__(self, config: Config):
        self.config = config
        self.model = None
        self.guide_questions = parse_interview_guide(config.guide_path) if config.guide_path else []

    def load_model(self):
        from faster_whisper import WhisperModel
        from rich.console import Console
        console = Console()

        device, compute_type = detect_device(self.config.device)
        self.config.device = device
        self.config.compute_type = compute_type

        with console.status(f"[bold cyan]Loading {self.config.model_size} on {device} ({compute_type})..."):
            self.model = WhisperModel(
                self.config.model_size,
                device=device,
                compute_type=compute_type,
                download_root=os.path.expanduser("~/.cache/whisper-models"),
            )
        console.print(f"[green]✓[/green] Model: {self.config.model_size} ({device}, {compute_type})")

    def transcribe(self, audio_path: str) -> list[Segment]:
        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
        console = Console()

        if self.model is None:
            self.load_model()

        console.print(f"\n[bold]Transcribing:[/bold] {os.path.basename(audio_path)}")
        start_time = time.time()

        segments_gen, info = self.model.transcribe(
            audio_path,
            language=self.config.language,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            patience=self.config.patience,
            vad_filter=self.config.vad_filter,
            vad_parameters=self.config.vad_parameters,
            word_timestamps=True,
            condition_on_previous_text=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            initial_prompt=INITIAL_PROMPT,
        )

        duration = info.duration
        console.print(
            f"[dim]Duration: {duration / 60:.1f} min | "
            f"Language: {info.language} ({info.language_probability:.0%})[/dim]"
        )

        raw = []
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(), console=console,
        ) as progress:
            task = progress.add_task("Processing...", total=duration)
            for seg in segments_gen:
                raw.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip()))
                progress.update(task, completed=min(seg.end, duration))
            progress.update(task, completed=duration)

        elapsed = time.time() - start_time
        speed = duration / elapsed if elapsed > 0 else 0
        console.print(f"[green]✓[/green] Done in {elapsed:.1f}s ({speed:.1f}x realtime)")
        return raw

    def merge_segments(self, segments: list[Segment]) -> list[Segment]:
        """Merge fragmented whisper output into complete sentences."""
        if not segments:
            return []

        merged = []
        current = Segment(start=segments[0].start, end=segments[0].end, text=segments[0].text)

        for seg in segments[1:]:
            gap = seg.start - current.end
            text = current.text.strip()
            ends_sentence = text and text[-1] in ".!?"
            should_merge = (gap <= self.config.merge_gap_threshold and not ends_sentence) or gap < 0.5

            if should_merge:
                current.end = seg.end
                current.text = text + " " + seg.text.strip()
            else:
                if len(current.text.split()) >= self.config.min_segment_words:
                    merged.append(current)
                elif merged:
                    merged[-1].end = current.end
                    merged[-1].text += " " + current.text.strip()
                current = Segment(start=seg.start, end=seg.end, text=seg.text.strip())

        if current.text.strip():
            if len(current.text.split()) >= self.config.min_segment_words:
                merged.append(current)
            elif merged:
                merged[-1].end = current.end
                merged[-1].text += " " + current.text.strip()

        return merged

    @staticmethod
    def clean_text(text: str) -> str:
        text = re.sub(r'\b(\w+)( \1\b){2,}', r'\1', text)
        text = re.sub(r'\s+([.,!?;:])', r'\1', text)
        text = re.sub(r'([.,!?;:])(\w)', r'\1 \2', text)
        text = re.sub(r'\s{2,}', ' ', text).strip()
        if text:
            text = text[0].upper() + text[1:]
            text = re.sub(r'(?<=[.!?]\s)(\w)', lambda m: m.group(1).upper(), text)
            if text[-1] not in '.!?':
                text += '.'
        return text

    def detect_speakers(self, segments: list[Segment]) -> list[Segment]:
        if not segments:
            return []

        turn_points = {
            i for i in range(1, len(segments))
            if segments[i].start - segments[i - 1].end >= self.config.pause_for_turn_switch
        }

        speaker = "interviewer"
        segments[0].speaker = speaker
        for i in range(1, len(segments)):
            if i in turn_points:
                speaker = "respondent" if speaker == "interviewer" else "interviewer"
            segments[i].speaker = speaker

        return segments

    def structure(self, segments: list[Segment]) -> list[dict]:
        if not segments:
            return []

        blocks = []
        q_count = 0
        current = None

        for seg in segments:
            btype = "question" if seg.speaker == "interviewer" else "answer"
            if current is None or btype != current["type"]:
                if current:
                    blocks.append(current)
                if btype == "question":
                    q_count += 1
                current = {
                    "type": btype, "number": q_count,
                    "text": seg.text, "start": seg.start, "end": seg.end,
                }
            else:
                current["text"] += " " + seg.text
                current["end"] = seg.end

        if current:
            blocks.append(current)

        for block in blocks:
            block["text"] = self.clean_text(block["text"])

        if self.guide_questions:
            idx = 0
            for block in blocks:
                if block["type"] == "question" and idx < len(self.guide_questions):
                    block["guide_question"] = self.guide_questions[idx]["text"]
                    idx += 1

        return blocks

    def process(self, audio_path: str) -> list[dict]:
        from rich.console import Console
        console = Console()

        raw = self.transcribe(audio_path)
        console.print(f"  Segments: {len(raw)} raw", end="")

        merged = self.merge_segments(raw)
        console.print(f" → {len(merged)} merged", end="")

        self.detect_speakers(merged)
        blocks = self.structure(merged)
        console.print(f" → {len(blocks)} Q&A blocks")
        return blocks


# ──────────────────────────────────────────────────────────────
# Output Formatters
# ──────────────────────────────────────────────────────────────

def fmt_ts(seconds: float) -> str:
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def save_docx(blocks: list[dict], path: str, source: str, guide: list[dict] = None):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    for margin in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(doc.sections[0], margin, Cm(2.5))

    title = doc.add_heading("Interview Transcript", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    meta = doc.add_paragraph()
    meta_lines = [f"Source: {source}", f"Transcribed: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    if guide:
        meta_lines.append(f"Interview guide: {len(guide)} questions")
    for line in meta_lines:
        run = meta.add_run(line + "\n")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(100, 100, 100)

    doc.add_paragraph()
    sep = doc.add_paragraph()
    r = sep.add_run("─" * 60)
    r.font.color.rgb = RGBColor(180, 180, 180)
    r.font.size = Pt(8)
    doc.add_paragraph()

    for block in blocks:
        is_q = block["type"] == "question"
        label = f"{'Q' if is_q else 'A'}{block['number']}"
        color = RGBColor(30, 60, 120) if is_q else RGBColor(40, 100, 40)

        header = doc.add_paragraph()
        header.space_before = Pt(18 if is_q else 4)
        header.space_after = Pt(4 if is_q else 2)

        lr = header.add_run(label)
        lr.bold = True
        lr.font.size = Pt(12 if is_q else 11)
        lr.font.color.rgb = color

        ts = header.add_run(f"  [{fmt_ts(block['start'])}]")
        ts.font.size = Pt(9)
        ts.font.color.rgb = RGBColor(150, 150, 150)

        if is_q and "guide_question" in block:
            gp = doc.add_paragraph()
            gp.space_before = Pt(0)
            gp.space_after = Pt(2)
            gr = gp.add_run(f"Guide: {block['guide_question']}")
            gr.font.size = Pt(9)
            gr.font.italic = True
            gr.font.color.rgb = RGBColor(130, 130, 130)

        body = doc.add_paragraph()
        body.space_before = Pt(2)
        body.space_after = Pt(6 if is_q else 12)
        br = body.add_run(block["text"])
        br.font.size = Pt(11)
        if is_q:
            br.bold = True

    doc.save(path)


def save_txt(blocks: list[dict], path: str, source: str):
    lines = [f"INTERVIEW TRANSCRIPT — {source}", "=" * 60, ""]
    for block in blocks:
        prefix = f"{'Q' if block['type'] == 'question' else 'A'}{block['number']}"
        lines.append(f"{prefix} [{fmt_ts(block['start'])}]:")
        if "guide_question" in block:
            lines.append(f"  (Guide: {block['guide_question']})")
        lines.append(block["text"])
        lines.append("")
        if block["type"] == "answer":
            lines.extend(["-" * 40, ""])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_json(blocks: list[dict], path: str, source: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "blocks": blocks}, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interview Transcription System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python transcribe.py interview.mp3
  python transcribe.py interview.mp3 --guide questions.txt
  python transcribe.py interview.mp3 -o output.docx -f both
  python transcribe.py ./interviews/
  python transcribe.py interview.mp3 --language en --model medium
        """,
    )

    parser.add_argument("input", nargs="?", default=".", help="Audio file or directory (default: current directory)")
    parser.add_argument("--guide", "-g", help="Interview guide file")
    parser.add_argument("--output", "-o", help="Output path (auto-generated if omitted)")
    parser.add_argument("--format", "-f", choices=["docx", "txt", "both", "json"], default="docx")
    parser.add_argument("--model", "-m", default=MODEL_SIZE, help=f"Whisper model (default: {MODEL_SIZE})")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto",
                        help="Device selection (default: auto)")
    parser.add_argument("--language", "-l", default=LANGUAGE, help=f"Language code (default: {LANGUAGE})")
    parser.add_argument("--beam-size", type=int, default=BEAM_SIZE, help=f"Beam size (default: {BEAM_SIZE})")
    parser.add_argument("--pause-threshold", type=float, default=PAUSE_THRESHOLD,
                        help=f"Speaker change pause in seconds (default: {PAUSE_THRESHOLD})")

    args = parser.parse_args()
    check_dependencies()

    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    console.print(Panel.fit(
        f"[bold cyan]Interview Transcription System[/bold cyan]\n"
        f"Model: {args.model} | Device: {args.device} | Language: {args.language}",
        border_style="cyan",
    ))

    config = Config(
        model_size=args.model,
        device=args.device,
        language=args.language,
        beam_size=args.beam_size,
        guide_path=args.guide,
        output_format=args.format,
        pause_for_turn_switch=args.pause_threshold,
    )

    input_path = Path(args.input)
    if input_path.is_dir():
        audio_files = sorted(f for f in input_path.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS)
        if not audio_files:
            console.print(f"[red]No audio files found in {input_path}[/red]")
            sys.exit(1)
        console.print(f"Found [bold]{len(audio_files)}[/bold] audio files")
    elif input_path.is_file():
        audio_files = [input_path]
    else:
        console.print(f"[red]Not found: {args.input}[/red]")
        sys.exit(1)

    transcriber = Transcriber(config)

    for i, audio_file in enumerate(audio_files, 1):
        console.print(f"\n{'═' * 60}")
        console.print(f"[bold]File {i}/{len(audio_files)}:[/bold] {audio_file.name}")
        console.print(f"{'═' * 60}")

        blocks = transcriber.process(str(audio_file))

        if args.output and len(audio_files) == 1:
            out_base, out_dir = Path(args.output).stem, Path(args.output).parent
        else:
            out_base, out_dir = audio_file.stem + "_transcript", audio_file.parent

        if args.format in ("docx", "both"):
            p = out_dir / f"{out_base}.docx"
            save_docx(blocks, str(p), audio_file.name, transcriber.guide_questions)
            console.print(f"[green]✓[/green] {p}")

        if args.format in ("txt", "both"):
            p = out_dir / f"{out_base}.txt"
            save_txt(blocks, str(p), audio_file.name)
            console.print(f"[green]✓[/green] {p}")

        if args.format == "json":
            p = out_dir / f"{out_base}.json"
            save_json(blocks, str(p), audio_file.name)
            console.print(f"[green]✓[/green] {p}")

        console.print(f"\n[bold]Preview:[/bold]")
        for block in blocks[:6]:
            label = f"{'Q' if block['type'] == 'question' else 'A'}{block['number']}"
            style = "bold blue" if block["type"] == "question" else "bold green"
            text = block["text"][:150] + ("..." if len(block["text"]) > 150 else "")
            console.print(f"  [{style}]{label}[/{style}] [{fmt_ts(block['start'])}]: {text}")

    console.print(f"\n[bold green]Done.[/bold green] Processed {len(audio_files)} file(s).")


if __name__ == "__main__":
    main()
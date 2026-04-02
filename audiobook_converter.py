#!/usr/bin/env python3
"""
Audiobook Converter for the Bullied Kids Series
================================================
Converts .docx or .txt manuscripts into chapter-by-chapter audiobook MP3s.

Supports multiple FREE TTS engines (pick whichever sounds best to you):

1. edge-tts     - Microsoft Edge voices (FREE, best quality, needs internet)
2. pyttsx3      - Offline, uses OS built-in voices (SAPI5 on Windows, espeak on Linux)
3. gTTS         - Google Translate TTS (FREE, needs internet, decent quality)
4. Coqui TTS    - Open-source neural TTS (FREE, offline, GPU recommended)

Usage:
    pip install edge-tts pydub python-docx
    python audiobook_converter.py --input "manuscript.docx" --engine edge-tts

    # Or for a folder of manuscripts:
    python audiobook_converter.py --input ./manuscripts/ --engine edge-tts

Author: Ricky Carter / Ricky's Automations
"""

import argparse
import asyncio
import os
import re
import sys
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AudiobookConfig:
    """Settings for audiobook production."""
    # Audio specs (ACX / Audible requirements)
    sample_rate: int = 44100          # 44.1 kHz
    bitrate: str = "192k"            # 192 kbps CBR
    channels: int = 1                 # Mono
    format: str = "mp3"

    # Chapter detection patterns
    chapter_patterns: list = field(default_factory=lambda: [
        r"^Chapter\s+\d+",           # Chapter 1, Chapter 23
        r"^CHAPTER\s+\d+",           # CHAPTER 1
        r"^Part\s+\w+",              # Part One, Part 1
        r"^PART\s+\w+",              # PART ONE
        r"^Prologue",                # Prologue
        r"^Epilogue",                # Epilogue
        r"^PROLOGUE",
        r"^EPILOGUE",
    ])

    # Pauses (in milliseconds of silence to insert)
    pause_paragraph: int = 600        # Between paragraphs
    pause_chapter: int = 2000         # Between chapters
    pause_scene_break: int = 1500     # At scene breaks (e.g., "***" or "---")

    # Edge-TTS voice options (Microsoft Neural voices)
    # Run: edge-tts --list-voices  to see all options
    edge_voice: str = "en-US-AndrewNeural"  # Deep, male narrator voice
    edge_rate: str = "-5%"               # Slightly slower for audiobook pacing
    edge_pitch: str = "+0Hz"

    # Output
    output_dir: str = "./audiobook_output"
    add_chapter_silence: bool = True      # 0.5s silence at start/end per ACX


# ---------------------------------------------------------------------------
# Manuscript Reader
# ---------------------------------------------------------------------------

def read_docx(filepath: str) -> str:
    """Extract text from a .docx file."""
    try:
        from docx import Document
    except ImportError:
        print("ERROR: python-docx not installed. Run: pip install python-docx")
        sys.exit(1)

    doc = Document(filepath)
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
        else:
            paragraphs.append("")  # Preserve blank lines for scene breaks
    return "\n".join(paragraphs)


def read_txt(filepath: str) -> str:
    """Read a plain text file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def read_manuscript(filepath: str) -> str:
    """Read manuscript from supported formats."""
    ext = Path(filepath).suffix.lower()
    if ext == ".docx":
        return read_docx(filepath)
    elif ext in (".txt", ".md"):
        return read_txt(filepath)
    else:
        print(f"ERROR: Unsupported file format '{ext}'. Use .docx, .txt, or .md")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Chapter Splitter
# ---------------------------------------------------------------------------

@dataclass
class Chapter:
    number: int
    title: str
    text: str


def split_into_chapters(text: str, config: AudiobookConfig) -> list[Chapter]:
    """Split manuscript text into chapters based on heading patterns."""
    combined_pattern = "|".join(f"({p})" for p in config.chapter_patterns)
    lines = text.split("\n")

    chapters = []
    current_title = "Front Matter"
    current_lines = []
    chapter_num = 0

    for line in lines:
        stripped = line.strip()
        if stripped and re.match(combined_pattern, stripped, re.IGNORECASE):
            # Save previous chapter if it has content
            content = "\n".join(current_lines).strip()
            if content:
                chapters.append(Chapter(
                    number=chapter_num,
                    title=current_title,
                    text=content
                ))
            chapter_num += 1
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    # Don't forget the last chapter
    content = "\n".join(current_lines).strip()
    if content:
        chapters.append(Chapter(
            number=chapter_num,
            title=current_title,
            text=content
        ))

    if not chapters:
        # No chapter headings found, treat entire text as one chapter
        chapters.append(Chapter(number=1, title="Full Text", text=text.strip()))

    return chapters


# ---------------------------------------------------------------------------
# Text Preprocessor (clean up for narration)
# ---------------------------------------------------------------------------

def preprocess_for_narration(text: str) -> str:
    """Clean and prepare text for TTS narration."""
    # Replace em dashes with commas (per your preference)
    text = text.replace("—", ",")
    text = text.replace("--", ",")

    # Replace scene break markers with a pause marker
    text = re.sub(r"\n\s*(\*\s*\*\s*\*|\*{3,}|-{3,}|#\s*#\s*#)\s*\n",
                  "\n[SCENE_BREAK]\n", text)

    # Clean up multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove any stray formatting artifacts
    text = text.replace("***", "")
    text = text.replace("**", "")
    text = text.replace("*", "")

    # Expand common abbreviations for better pronunciation
    replacements = {
        "Mr.": "Mister",
        "Mrs.": "Missus",
        "Dr.": "Doctor",
        "St.": "Saint",
        "vs.": "versus",
        "etc.": "et cetera",
        "Jr.": "Junior",
        "Sr.": "Senior",
    }
    for abbr, full in replacements.items():
        text = text.replace(abbr, full)

    return text.strip()


# ---------------------------------------------------------------------------
# TTS Engine: edge-tts (RECOMMENDED)
# ---------------------------------------------------------------------------

async def generate_edge_tts(text: str, output_path: str, config: AudiobookConfig):
    """Generate audio using Microsoft Edge TTS (free, high quality)."""
    try:
        import edge_tts
    except ImportError:
        print("ERROR: edge-tts not installed. Run: pip install edge-tts")
        sys.exit(1)

    communicate = edge_tts.Communicate(
        text,
        voice=config.edge_voice,
        rate=config.edge_rate,
        pitch=config.edge_pitch
    )
    await communicate.save(output_path)


# ---------------------------------------------------------------------------
# TTS Engine: pyttsx3 (offline)
# ---------------------------------------------------------------------------

def generate_pyttsx3(text: str, output_path: str, config: AudiobookConfig):
    """Generate audio using pyttsx3 (offline, uses system voices)."""
    try:
        import pyttsx3
    except ImportError:
        print("ERROR: pyttsx3 not installed. Run: pip install pyttsx3")
        sys.exit(1)

    engine = pyttsx3.init()
    engine.setProperty("rate", 160)  # Words per minute (slower for audiobooks)
    engine.setProperty("volume", 1.0)

    # Try to pick a good voice
    voices = engine.getProperty("voices")
    for voice in voices:
        if "david" in voice.name.lower() or "guy" in voice.name.lower():
            engine.setProperty("voice", voice.id)
            break

    engine.save_to_file(text, output_path)
    engine.runAndWait()


# ---------------------------------------------------------------------------
# TTS Engine: gTTS (Google)
# ---------------------------------------------------------------------------

def generate_gtts(text: str, output_path: str, config: AudiobookConfig):
    """Generate audio using Google Text-to-Speech (free, needs internet)."""
    try:
        from gtts import gTTS
    except ImportError:
        print("ERROR: gTTS not installed. Run: pip install gTTS")
        sys.exit(1)

    tts = gTTS(text=text, lang="en", slow=False)
    tts.save(output_path)


# ---------------------------------------------------------------------------
# Audio Post-Processing
# ---------------------------------------------------------------------------

def postprocess_audio(input_path: str, output_path: str, config: AudiobookConfig):
    """
    Post-process audio to meet ACX/Audible specifications:
    - 192 kbps CBR MP3
    - 44.1 kHz sample rate
    - Mono
    - Add room tone (silence) at start/end
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        print("WARNING: pydub not installed. Skipping post-processing.")
        print("         Run: pip install pydub")
        print("         Also install ffmpeg: https://ffmpeg.org/download.html")
        # Just copy the raw file
        if input_path != output_path:
            import shutil
            shutil.copy2(input_path, output_path)
        return

    audio = AudioSegment.from_file(input_path)

    # Convert to mono
    audio = audio.set_channels(config.channels)

    # Set sample rate
    audio = audio.set_frame_rate(config.sample_rate)

    # Add 0.5s silence at start and 1s at end (ACX requirement)
    if config.add_chapter_silence:
        silence_start = AudioSegment.silent(duration=500, frame_rate=config.sample_rate)
        silence_end = AudioSegment.silent(duration=1000, frame_rate=config.sample_rate)
        audio = silence_start + audio + silence_end

    # Export as CBR MP3
    audio.export(
        output_path,
        format=config.format,
        bitrate=config.bitrate,
        parameters=["-ar", str(config.sample_rate), "-ac", str(config.channels)]
    )


# ---------------------------------------------------------------------------
# Batch Processor
# ---------------------------------------------------------------------------

def get_safe_filename(title: str) -> str:
    """Convert chapter title to a safe filename."""
    safe = re.sub(r'[^\w\s-]', '', title)
    safe = re.sub(r'\s+', '_', safe.strip())
    return safe[:80]


async def convert_chapter_edge(chapter: Chapter, output_dir: str, book_title: str,
                                config: AudiobookConfig) -> str:
    """Convert a single chapter using edge-tts."""
    filename = f"{chapter.number:02d}_{get_safe_filename(chapter.title)}"
    raw_path = os.path.join(output_dir, f"{filename}_raw.mp3")
    final_path = os.path.join(output_dir, f"{filename}.mp3")

    # Prepare narration text with chapter announcement
    narration = f"{chapter.title}.\n\n{preprocess_for_narration(chapter.text)}"

    print(f"  Generating audio for: {chapter.title} ({len(narration)} chars)...")
    start = time.time()

    await generate_edge_tts(narration, raw_path, config)

    # Post-process to meet ACX specs
    postprocess_audio(raw_path, final_path, config)

    # Clean up raw file
    if os.path.exists(raw_path) and raw_path != final_path:
        os.remove(raw_path)

    elapsed = time.time() - start
    print(f"  Done: {chapter.title} ({elapsed:.1f}s)")
    return final_path


def convert_chapter_sync(chapter: Chapter, output_dir: str, book_title: str,
                          config: AudiobookConfig, engine: str) -> str:
    """Convert a single chapter using a synchronous TTS engine."""
    filename = f"{chapter.number:02d}_{get_safe_filename(chapter.title)}"
    raw_path = os.path.join(output_dir, f"{filename}_raw.mp3")
    final_path = os.path.join(output_dir, f"{filename}.mp3")

    narration = f"{chapter.title}.\n\n{preprocess_for_narration(chapter.text)}"

    print(f"  Generating audio for: {chapter.title} ({len(narration)} chars)...")
    start = time.time()

    if engine == "pyttsx3":
        # pyttsx3 outputs wav, so adjust paths
        raw_path = raw_path.replace(".mp3", ".wav")
        generate_pyttsx3(narration, raw_path, config)
    elif engine == "gtts":
        generate_gtts(narration, raw_path, config)

    postprocess_audio(raw_path, final_path, config)

    if os.path.exists(raw_path) and raw_path != final_path:
        os.remove(raw_path)

    elapsed = time.time() - start
    print(f"  Done: {chapter.title} ({elapsed:.1f}s)")
    return final_path


# ---------------------------------------------------------------------------
# Metadata & Manifest
# ---------------------------------------------------------------------------

def write_manifest(book_title: str, chapters: list[Chapter],
                   audio_files: list[str], output_dir: str):
    """Write a JSON manifest for the audiobook (useful for uploading to ACX)."""
    manifest = {
        "title": book_title,
        "author": "Ricky Carter",
        "narrator": "AI-Generated",
        "chapters": []
    }

    for chapter, audio_file in zip(chapters, audio_files):
        manifest["chapters"].append({
            "number": chapter.number,
            "title": chapter.title,
            "file": os.path.basename(audio_file),
            "text_length": len(chapter.text)
        })

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest written to: {manifest_path}")


# ---------------------------------------------------------------------------
# Voice Listing Helper
# ---------------------------------------------------------------------------

async def list_voices():
    """List available edge-tts voices for selection."""
    try:
        import edge_tts
    except ImportError:
        print("Install edge-tts first: pip install edge-tts")
        return

    voices = await edge_tts.list_voices()
    en_voices = [v for v in voices if v["Locale"].startswith("en-")]

    print("\n=== Available English Voices (edge-tts) ===\n")
    print(f"{'Name':<35} {'Gender':<8} {'Locale':<8}")
    print("-" * 55)
    for v in sorted(en_voices, key=lambda x: x["ShortName"]):
        print(f"{v['ShortName']:<35} {v['Gender']:<8} {v['Locale']:<8}")

    print(f"\nTotal: {len(en_voices)} English voices")
    print("\nRecommended for audiobooks:")
    print("  Male:   en-US-GuyNeural, en-US-AndrewNeural, en-GB-RyanNeural")
    print("  Female: en-US-JennyNeural, en-US-AriaNeural, en-GB-SoniaNeural")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def process_book(filepath: str, config: AudiobookConfig, engine: str):
    """Process a single book into audiobook chapters."""
    book_title = Path(filepath).stem
    book_output_dir = os.path.join(config.output_dir, get_safe_filename(book_title))
    os.makedirs(book_output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Processing: {book_title}")
    print(f"  Engine: {engine}")
    print(f"  Output: {book_output_dir}")
    print(f"{'='*60}")

    # Read manuscript
    print("\nReading manuscript...")
    text = read_manuscript(filepath)
    print(f"  Total length: {len(text):,} characters")

    # Split into chapters
    print("\nSplitting into chapters...")
    chapters = split_into_chapters(text, config)
    print(f"  Found {len(chapters)} chapters:")
    for ch in chapters:
        print(f"    {ch.number:2d}. {ch.title} ({len(ch.text):,} chars)")

    # Convert each chapter
    print(f"\nConverting chapters to audio...")
    audio_files = []

    for chapter in chapters:
        if engine == "edge-tts":
            path = await convert_chapter_edge(chapter, book_output_dir, book_title, config)
        else:
            path = convert_chapter_sync(chapter, book_output_dir, book_title, config, engine)
        audio_files.append(path)

    # Write manifest
    write_manifest(book_title, chapters, audio_files, book_output_dir)

    print(f"\n{'='*60}")
    print(f"  COMPLETE: {book_title}")
    print(f"  {len(audio_files)} audio files in: {book_output_dir}")
    print(f"{'='*60}")

    return audio_files


async def main():
    parser = argparse.ArgumentParser(
        description="Convert manuscripts to audiobook MP3s (Bullied Kids Series)"
    )
    parser.add_argument("--input", "-i", required=True,
                        help="Path to .docx/.txt file or folder of manuscripts")
    parser.add_argument("--engine", "-e", default="edge-tts",
                        choices=["edge-tts", "pyttsx3", "gtts"],
                        help="TTS engine to use (default: edge-tts)")
    parser.add_argument("--voice", "-v", default=None,
                        help="Voice name (edge-tts only, e.g. en-US-GuyNeural)")
    parser.add_argument("--rate", default="-5%",
                        help="Speech rate adjustment (edge-tts only, e.g. -10%%)")
    parser.add_argument("--output", "-o", default="./audiobook_output",
                        help="Output directory (default: ./audiobook_output)")
    parser.add_argument("--list-voices", action="store_true",
                        help="List available edge-tts voices and exit")

    args = parser.parse_args()

    # List voices mode
    if args.list_voices:
        await list_voices()
        return

    # Configure
    config = AudiobookConfig(
        output_dir=args.output,
        edge_rate=args.rate,
    )
    if args.voice:
        config.edge_voice = args.voice

    # Process input
    input_path = Path(args.input)

    if input_path.is_file():
        await process_book(str(input_path), config, args.engine)

    elif input_path.is_dir():
        manuscripts = list(input_path.glob("*.docx")) + list(input_path.glob("*.txt"))
        if not manuscripts:
            print(f"No .docx or .txt files found in {input_path}")
            sys.exit(1)

        print(f"Found {len(manuscripts)} manuscripts:")
        for m in manuscripts:
            print(f"  - {m.name}")

        for manuscript in sorted(manuscripts):
            await process_book(str(manuscript), config, args.engine)

    else:
        print(f"ERROR: '{args.input}' is not a valid file or directory")
        sys.exit(1)

    print("\n\nAll done! Next steps:")
    print("  1. Listen through each chapter for quality")
    print("  2. Check audio levels meet ACX specs (RMS between -23dB and -18dB)")
    print("  3. Upload to ACX, Findaway Voices, or Authors Republic")
    print("  4. Consider adding chapter markers with tools like Chapter and Verse")


if __name__ == "__main__":
    asyncio.run(main())

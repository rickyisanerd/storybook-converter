#!/usr/bin/env python3
"""
Flask web application for the Audiobook Converter.
Provides a browser-based interface for uploading manuscripts and
generating chapter-by-chapter MP3 audiobooks via edge-tts / pyttsx3 / gTTS.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from audiobook_converter import (
    AudiobookConfig,
    _PYDUB_AVAILABLE,
    _PYDUB_IMPORT_ERROR,
    convert_chapter_edge,
    convert_chapter_sync,
    get_safe_filename,
    read_manuscript,
    split_into_chapters,
    write_manifest,
)

# ---------------------------------------------------------------------------
# Startup dependency check
# ---------------------------------------------------------------------------

if not _PYDUB_AVAILABLE:
    logging.error(
        "STARTUP CHECK FAILED: pydub is not importable — audio post-processing "
        "will raise an error for every conversion job. "
        "Import error was: %s. "
        "Verify that 'pydub' is listed in requirements.txt and that the package "
        "was installed successfully (check build logs). "
        "ffmpeg must also be present on PATH for pydub to function.",
        _PYDUB_IMPORT_ERROR,
    )
else:
    logging.info("Startup check passed: pydub imported successfully.")

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path("./uploads")
OUTPUT_DIR = Path("./audiobook_output")
ALLOWED_EXTENSIONS = {".docx", ".txt", ".md"}

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------
# Structure per job:
# {
#   "status":   "pending" | "processing" | "complete" | "error",
#   "progress": {"completed": int, "total": int},
#   "chapters": [{"number": int, "title": str, "file": str, "done": bool}],
#   "output_dir": str,
#   "book_title": str,
#   "error": str | None,
# }

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _update_job(job_id: str, **kwargs):
    with jobs_lock:
        jobs[job_id].update(kwargs)


# ---------------------------------------------------------------------------
# Background conversion worker
# ---------------------------------------------------------------------------

def _run_conversion(job_id: str, manuscript_path: str, engine: str,
                    voice: str, rate: str):
    """Run the full conversion pipeline in a background thread."""
    try:
        _update_job(job_id, status="processing")

        config = AudiobookConfig(
            output_dir=str(OUTPUT_DIR),
            edge_voice=voice,
            edge_rate=rate,
        )

        book_title = Path(manuscript_path).stem
        book_output_dir = OUTPUT_DIR / get_safe_filename(book_title)
        book_output_dir.mkdir(parents=True, exist_ok=True)

        # Read & split
        text = read_manuscript(manuscript_path)
        chapters = split_into_chapters(text, config)

        chapter_meta = [
            {"number": ch.number, "title": ch.title, "file": None, "done": False}
            for ch in chapters
        ]
        _update_job(
            job_id,
            book_title=book_title,
            output_dir=str(book_output_dir),
            chapters=chapter_meta,
            progress={"completed": 0, "total": len(chapters)},
        )

        audio_files = []

        for idx, chapter in enumerate(chapters):
            if engine == "edge-tts":
                # edge-tts is async; run it inside a fresh event loop
                path = asyncio.run(
                    convert_chapter_edge(
                        chapter, str(book_output_dir), book_title, config
                    )
                )
            else:
                path = convert_chapter_sync(
                    chapter, str(book_output_dir), book_title, config, engine
                )

            audio_files.append(path)

            with jobs_lock:
                jobs[job_id]["chapters"][idx]["file"] = os.path.basename(path)
                jobs[job_id]["chapters"][idx]["done"] = True
                jobs[job_id]["progress"]["completed"] = idx + 1

        write_manifest(book_title, chapters, audio_files, str(book_output_dir))

        _update_job(job_id, status="complete")

    except Exception as exc:  # noqa: BLE001
        _update_job(job_id, status="error", error=str(exc))
    finally:
        # Clean up the uploaded manuscript
        try:
            os.remove(manuscript_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the upload form."""
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    """
    Accept a manuscript file and conversion options, then kick off a
    background conversion job.

    Form fields:
        file     – the manuscript (.docx / .txt)
        engine   – tts engine: edge-tts | pyttsx3 | gtts
        voice    – edge-tts voice name (ignored for other engines)
        rate     – speech rate adjustment, e.g. "-5%"

    Returns JSON: { "job_id": "<uuid>" }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    suffix = Path(f.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify(
            {"error": f"Unsupported file type '{suffix}'. Use .docx or .txt"}
        ), 400

    engine = request.form.get("engine", "edge-tts")
    if engine not in ("edge-tts", "pyttsx3", "gtts"):
        return jsonify({"error": f"Unknown engine '{engine}'"}), 400

    voice = request.form.get("voice", "en-US-AndrewNeural")
    rate = request.form.get("rate", "-5%")

    # Persist the upload
    job_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{job_id}{suffix}"
    f.save(str(save_path))

    # Register job
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "progress": {"completed": 0, "total": 0},
            "chapters": [],
            "output_dir": None,
            "book_title": None,
            "error": None,
        }

    # Start background thread
    t = threading.Thread(
        target=_run_conversion,
        args=(job_id, str(save_path), engine, voice, rate),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id}), 202


@app.route("/api/status/<job_id>")
def status(job_id: str):
    """
    Return the current state of a conversion job.

    Response JSON:
    {
        "job_id":   str,
        "status":   "pending" | "processing" | "complete" | "error",
        "progress": {"completed": int, "total": int},
        "chapters": [{"number": int, "title": str, "file": str|null, "done": bool}],
        "book_title": str | null,
        "error":    str | null
    }
    """
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({"job_id": job_id, **job})


@app.route("/api/download/<job_id>/<filename>")
def download(job_id: str, filename: str):
    """Serve a generated audio file for download."""
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] != "complete":
        return jsonify({"error": "Job not yet complete"}), 409

    output_dir = job.get("output_dir")
    if not output_dir or not os.path.isdir(output_dir):
        return jsonify({"error": "Output directory not found"}), 404

    return send_from_directory(output_dir, filename, as_attachment=True)


@app.route("/api/manifest/<job_id>")
def manifest(job_id: str):
    """Return the audiobook manifest JSON for a completed job."""
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] != "complete":
        return jsonify({"error": "Job not yet complete"}), 409

    output_dir = job.get("output_dir")
    manifest_path = os.path.join(output_dir, "manifest.json") if output_dir else None

    if not manifest_path or not os.path.isfile(manifest_path):
        return jsonify({"error": "Manifest not found"}), 404

    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    return jsonify(data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

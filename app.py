import asyncio
import os
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

app = FastAPI(title="Audiobook Converter")

# In-memory job store
jobs: dict = {}


async def run_conversion(job_id: str, input_path: str, tmp_dir: str, voice: str, rate: str):
    from audiobook_converter import (
        AudiobookConfig,
        convert_chapter_edge,
        read_manuscript,
        split_into_chapters,
    )

    jobs[job_id]["status"] = "processing"

    try:
        config = AudiobookConfig(output_dir=tmp_dir, edge_voice=voice, edge_rate=rate)

        text = read_manuscript(input_path)
        chapters = split_into_chapters(text, config)

        jobs[job_id]["total"] = len(chapters)

        book_title = Path(input_path).stem
        book_output_dir = os.path.join(tmp_dir, book_title)
        os.makedirs(book_output_dir, exist_ok=True)

        for i, chapter in enumerate(chapters):
            await convert_chapter_edge(chapter, book_output_dir, book_title, config)
            jobs[job_id]["completed"] = i + 1

        # Zip up all MP3s
        zip_path = os.path.join(tmp_dir, f"{book_title}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(Path(book_output_dir).glob("*.mp3")):
                zf.write(f, f.name)
            manifest = Path(book_output_dir) / "manifest.json"
            if manifest.exists():
                zf.write(manifest, "manifest.json")

        jobs[job_id].update({"status": "complete", "zip_path": zip_path, "book_title": book_title})

    except Exception as e:
        jobs[job_id].update({"status": "error", "error": str(e)})


@app.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    voice: str = Form("en-US-AndrewNeural"),
    rate: str = Form("-5%"),
):
    if not file.filename.lower().endswith((".docx", ".txt")):
        return JSONResponse({"error": "Only .docx and .txt files are supported."}, status_code=400)

    job_id = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, file.filename)

    with open(input_path, "wb") as f:
        f.write(await file.read())

    jobs[job_id] = {"status": "queued", "total": 0, "completed": 0, "tmp_dir": tmp_dir}
    background_tasks.add_task(run_conversion, job_id, input_path, tmp_dir, voice, rate)

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({
        "status": job["status"],
        "total": job.get("total", 0),
        "completed": job.get("completed", 0),
        "error": job.get("error"),
    })


@app.get("/download/{job_id}")
def download(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if job["status"] != "complete":
        return JSONResponse({"error": "Not ready yet"}, status_code=400)
    return FileResponse(
        job["zip_path"],
        media_type="application/zip",
        filename=f"{job['book_title']}.zip",
    )


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)

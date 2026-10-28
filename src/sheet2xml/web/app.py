"""HTTP API and upload page.

POST /api/jobs            upload a PDF or page images (field "files"), optional review=true
GET  /api/jobs/{id}       status and progress messages
GET  /api/jobs/{id}/result  the MusicXML file
GET  /api/health          what this server can do

Conversion runs in a small worker pool, because OMR takes a minute and a lot
of memory per job. Jobs live in memory and are removed after a day.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .. import __version__
from ..omr import IMAGE_EXT
from ..omr import available as omr_available
from ..pipeline import convert
from ..review import available as review_available

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = int(os.environ.get("SHEET2XML_MAX_UPLOAD_MB", "80")) * 1024 * 1024
KEEP_SECONDS = 24 * 3600
TOKEN = os.environ.get("SHEET2XML_TOKEN", "")

app = FastAPI(title="sheet2xml", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("SHEET2XML_WORKERS", "2")))


@dataclass
class Job:
    id: str
    dir: str
    files: list[str]
    review: bool
    status: str = "queued"  # queued, running, done, error
    messages: list[str] = field(default_factory=list)
    error: str = ""
    created: float = field(default_factory=time.time)
    result_name: str = ""
    info: dict = field(default_factory=dict)


jobs: dict[str, Job] = {}
lock = threading.Lock()


def check_token(request: Request) -> None:
    if not TOKEN:
        return
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    given = given or request.query_params.get("token", "")
    if given != TOKEN:
        raise HTTPException(401, "Zugangsschlüssel fehlt oder ist falsch")


def _cleanup() -> None:
    now = time.time()
    with lock:
        old = [j for j in jobs.values() if now - j.created > KEEP_SECONDS]
        for j in old:
            jobs.pop(j.id, None)
    for j in old:
        shutil.rmtree(j.dir, ignore_errors=True)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- äöüÄÖÜß]+", "_", Path(name).name).strip() or "datei"
    return name[:120]


def _run(job: Job) -> None:
    job.status = "running"
    try:
        res = convert(job.files, review=job.review, progress=job.messages.append)
        stem = res.title or Path(job.files[0]).stem.split("-", 1)[-1]
        stem = re.sub(r"\.(mxl|musicxml|xml|pdf|sib|dorico|musx)$", "", stem, flags=re.I)
        job.result_name = _safe_name(stem) + ".musicxml"
        Path(job.dir, "result.musicxml").write_text(res.musicxml, encoding="utf-8")
        job.info = {
            "engine": res.engine,
            "title": res.title,
            "parts": res.parts,
            "measures": res.measures,
            "flagged_measures": res.flagged_measures,
            "reviewed": res.reviewed,
            "warnings": res.warnings,
        }
        job.status = "done"
    except Exception as exc:  # report every failure to the client
        job.status = "error"
        job.error = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__


def _public(job: Job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "messages": job.messages,
        "error": job.error,
        "result": f"/api/jobs/{job.id}/result" if job.status == "done" else None,
        "filename": job.result_name,
        **job.info,
    }


@app.get("/api/health")
def health() -> dict:
    return {"version": __version__, "omr": omr_available(), "review": review_available(), "token": bool(TOKEN)}


@app.post("/api/jobs", dependencies=[Depends(check_token)])
async def create_job(files: list[UploadFile] = File(...), review: bool = Form(False)) -> dict:
    _cleanup()
    if not files:
        raise HTTPException(400, "Keine Datei hochgeladen")
    job_id = uuid.uuid4().hex
    tmp = tempfile.mkdtemp(prefix=f"job-{job_id[:8]}-")
    paths: list[str] = []
    total = 0
    pdfs = 0
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        if ext != ".pdf" and ext not in IMAGE_EXT:
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(415, f"Dateityp {ext or '?'} wird nicht unterstützt (PDF oder Bild)")
        pdfs += ext == ".pdf"
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(413, "Dateien sind zu groß")
        p = os.path.join(tmp, f"{i:03d}-{_safe_name(f.filename or 'seite')}")
        Path(p).write_bytes(data)
        paths.append(p)
    if pdfs and len(paths) > 1:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, "Bitte ein PDF allein hochladen oder mehrere Fotos (Seiten eines Stücks)")
    job = Job(id=job_id, dir=tmp, files=paths, review=review)
    with lock:
        jobs[job_id] = job
    pool.submit(_run, job)
    return _public(job)


def _get(job_id: str) -> Job:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Auftrag nicht gefunden")
    return job


@app.get("/api/jobs/{job_id}", dependencies=[Depends(check_token)])
def job_status(job_id: str) -> dict:
    return _public(_get(job_id))


@app.get("/api/jobs/{job_id}/result", dependencies=[Depends(check_token)])
def job_result(job_id: str) -> Response:
    job = _get(job_id)
    if job.status != "done":
        raise HTTPException(409, "Noch nicht fertig")
    return FileResponse(
        os.path.join(job.dir, "result.musicxml"),
        media_type="application/vnd.recordare.musicxml+xml",
        filename=job.result_name,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/icon.svg")
def icon() -> FileResponse:
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))

"""
HTTP surface for the converter.

    POST   /api/jobs               upload a .all file, get a job id back
    GET    /api/jobs/{id}          poll status, progress and summary
    GET    /api/jobs/{id}/download stream the finished LAS
    GET    /api/jobs/{id}/preview  thinned point sample for the plan view
    DELETE /api/jobs/{id}          discard a job and its files
    GET    /api/health             is MB-System actually present?
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .jobs import DONE, ERROR, JobStore

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("converter")

JOB_ROOT = Path(os.environ.get("JOB_ROOT", "/data/jobs"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 2 * 1024**3))  # 2 GiB
UPLOAD_CHUNK = 1024 * 1024

store: JobStore


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store
    store = JobStore(JOB_ROOT)
    store.sweep()
    log.info("job root %s, max upload %.1f GiB", JOB_ROOT, MAX_UPLOAD_BYTES / 1024**3)
    yield
    store.shutdown()


app = FastAPI(title="Multibeam .all to LAS Converter", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That job no longer exists.")
    return job


@app.get("/api/health")
def health() -> dict:
    """Confirms the real toolchain is present, not a mock."""
    mbinfo = shutil.which("mbinfo")
    version = ""
    if mbinfo:
        proc = subprocess.run(
            ["mbinfo", "-V"], capture_output=True, text=True, timeout=30
        )
        blob = proc.stdout or proc.stderr
        version = next(
            (ln.strip() for ln in blob.splitlines() if "Version" in ln), ""
        )
    return {
        "ok": bool(mbinfo),
        "mbinfo": mbinfo or "not found",
        "mbsystem_version": version,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@app.post("/api/jobs", status_code=202)
async def create_job(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "upload.all").name
    if not name.lower().endswith(".all"):
        raise HTTPException(
            status_code=400,
            detail="Only Kongsberg .all files are accepted.",
        )

    job = store.create(name)
    destination = job.work_dir / name
    written = 0

    # Streamed to disk in chunks: a multi-gigabyte survey must never be held
    # in memory, and the size cap is enforced as the bytes arrive.
    try:
        with destination.open("wb") as sink:
            while chunk := await file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File is larger than the "
                        f"{MAX_UPLOAD_BYTES / 1024**3:.1f} GiB limit.",
                    )
                sink.write(chunk)
    except HTTPException:
        store.delete(job.id)
        raise
    except Exception:
        store.delete(job.id)
        log.exception("upload failed")
        raise HTTPException(status_code=500, detail="Upload failed.")
    finally:
        await file.close()

    if written == 0:
        store.delete(job.id)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    job.upload_bytes = written
    store.submit(job, destination)
    return job.as_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    return _require_job(job_id).as_dict()


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = _require_job(job_id)
    if job.status == ERROR:
        raise HTTPException(status_code=409, detail=job.error or "Conversion failed.")
    if job.status != DONE or not job.las_path.exists():
        raise HTTPException(status_code=409, detail="This conversion is not finished.")
    return FileResponse(
        job.las_path,
        media_type="application/octet-stream",
        filename=job.download_name,
    )


@app.get("/api/jobs/{job_id}/preview")
def preview(job_id: str):
    job = _require_job(job_id)
    if job.status != DONE or not job.preview_path.exists():
        raise HTTPException(status_code=409, detail="No preview available yet.")
    return FileResponse(job.preview_path, media_type="application/json")


@app.delete("/api/jobs/{job_id}")
def discard(job_id: str) -> JSONResponse:
    if not store.delete(job_id):
        raise HTTPException(status_code=404, detail="That job no longer exists.")
    return JSONResponse({"deleted": job_id})

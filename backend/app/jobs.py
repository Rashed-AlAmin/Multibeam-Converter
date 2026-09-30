"""
Job bookkeeping.

Conversions are long, so an upload cannot be answered synchronously. Each
upload becomes a Job with its own scratch directory; a single background
worker runs them one at a time and the browser polls for progress.

Only ONE conversion runs at a time, on purpose. MB-System is CPU- and
memory-hungry, and letting three users kick off three builds on a small
server is a reliable way to trigger the OOM killer. Extra jobs queue.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import pipeline

log = logging.getLogger(__name__)

# A finished job keeps its LAS around this long so the user can download it,
# then the whole directory is removed. Nothing is left behind on disk.
JOB_TTL_SECONDS = 60 * 60
SWEEP_INTERVAL_SECONDS = 5 * 60

QUEUED, RUNNING, DONE, ERROR = "queued", "running", "done", "error"


@dataclass
class Job:
    id: str
    original_name: str
    upload_bytes: int
    work_dir: Path
    status: str = QUEUED
    progress: float = 0.0
    message: str = "Queued"
    error: str | None = None
    summary: dict | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def las_path(self) -> Path:
        return self.work_dir / "output.las"

    @property
    def preview_path(self) -> Path:
        return self.work_dir / "preview.json"

    @property
    def download_name(self) -> str:
        return Path(self.original_name).with_suffix(".las").name

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "upload_bytes": self.upload_bytes,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "error": self.error,
            "summary": self.summary,
            "download_name": self.download_name,
            "elapsed_seconds": round(
                (self.finished_at or time.time()) - self.created_at, 1
            ),
        }


class JobStore:
    """Thread-safe registry of jobs plus the worker that drains them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convert")
        self._stop = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
        self._sweeper.start()

    # -- lifecycle ---------------------------------------------------------
    def create(self, original_name: str) -> Job:
        job_id = uuid.uuid4().hex
        work = self.root / job_id
        work.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, original_name=original_name, upload_bytes=0, work_dir=work)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, job: Job, source: Path) -> None:
        self._pool.submit(self._run, job, source)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.work_dir, ignore_errors=True)
        return True

    def shutdown(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- worker ------------------------------------------------------------
    def _run(self, job: Job, source: Path) -> None:
        job.status = RUNNING
        job.progress = 0.01
        job.message = "Starting"

        def progress(pct: float, msg: str) -> None:
            job.progress = pct
            job.message = msg

        try:
            summary = pipeline.convert(
                all_file=source,
                work=job.work_dir,
                out_las=job.las_path,
                progress=progress,
                preview_path=job.preview_path,
            )
            job.summary = summary.as_dict()
            job.status = DONE
            job.progress = 1.0
            job.message = "Complete"
        except pipeline.ConversionError as exc:
            job.status = ERROR
            job.error = str(exc)
            job.message = "Failed"
            log.warning("job %s failed: %s", job.id, exc)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            job.status = ERROR
            job.error = "An unexpected error occurred while converting this file."
            job.message = "Failed"
            log.exception("job %s crashed: %s", job.id, exc)
        finally:
            job.finished_at = time.time()
            self._clean_intermediates(job)

    @staticmethod
    def _clean_intermediates(job: Job) -> None:
        """Drop everything except the LAS and its preview.

        The .all upload and the .mb59 / .xyz intermediates are several times
        larger than the result and are useless once the job has finished.
        """
        keep = {job.las_path.name, job.preview_path.name}
        for path in job.work_dir.iterdir():
            if path.name in keep:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    # -- housekeeping ------------------------------------------------------
    def _sweep_loop(self) -> None:
        while not self._stop.wait(SWEEP_INTERVAL_SECONDS):
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                log.exception("job sweep failed")

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.finished_at and now - job.finished_at > JOB_TTL_SECONDS
            ]
        for job_id in expired:
            self.delete(job_id)
        # Also remove orphan directories left by a previous container run.
        with self._lock:
            known = set(self._jobs)
        for path in self.root.iterdir():
            if path.is_dir() and path.name not in known:
                age = now - path.stat().st_mtime
                if age > JOB_TTL_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)
        return len(expired)

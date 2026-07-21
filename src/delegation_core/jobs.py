"""
jobs.py — In-process background job store.

Any module can submit a blocking function as a daemon thread and get a job_id
back immediately. The server polls status via task_status().

Job IDs are in-memory only — they do not survive a server restart.
"""

import logging
import threading
import uuid
from datetime import datetime

logger = logging.getLogger("jobs")

_jobs: dict = {}
_lock = threading.Lock()


def submit(task_name: str, fn, *args, **kwargs) -> str:
    """Run fn(*args, **kwargs) in a daemon thread. Returns a job_id immediately."""
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "task": task_name,
            "status": "running",
            "started": datetime.now().isoformat(),
            "finished": None,
            "result": None,
            "error": None,
        }

    def _worker():
        try:
            result = fn(*args, **kwargs)
            update = {"status": "done", "result": result, "finished": datetime.now().isoformat()}
        except Exception as e:
            logger.error("Background job %s (%s) failed: %s", job_id, task_name, e)
            update = {"status": "error", "error": str(e), "finished": datetime.now().isoformat()}
        with _lock:
            _jobs[job_id].update(update)

    threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}").start()
    logger.info("Submitted background job %s: %s", job_id, task_name)
    return job_id


def get(job_id: str) -> dict | None:
    """Return a snapshot of a job dict, or None if not found."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def running_count() -> int:
    """Return the number of currently running background jobs."""
    with _lock:
        return sum(1 for j in _jobs.values() if j["status"] == "running")

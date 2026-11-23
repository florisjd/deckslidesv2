"""Durable SQLite job queue. Jobs survive restart: 'running' jobs with a stale
heartbeat are re-queued by recover()."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Callable
from ..db.models import Job

Handler = Callable[[Job, "JobContext"], None]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobContext:
    def __init__(self, sf, job_id: int):
        self.sf, self.job_id = sf, job_id

    def progress(self, p: float, msg: str = ""):
        with self.sf() as s:
            j = s.get(Job, self.job_id)
            j.progress, j.message, j.heartbeat_at = p, msg, _now()
            s.commit()

    def cancelled(self) -> bool:
        with self.sf() as s:
            return s.get(Job, self.job_id).state == "cancelled"


class JobQueue:
    def __init__(self, sf, max_attempts: int = 3):
        self.sf, self.handlers, self.max_attempts = sf, {}, max_attempts

    def register(self, kind: str, fn: Handler):
        self.handlers[kind] = fn

    def enqueue(self, kind: str, payload: dict, project_id: int | None = None) -> int:
        with self.sf() as s:
            j = Job(kind=kind, payload=payload, project_id=project_id)
            s.add(j)
            s.commit()
            return j.id

    def recover(self, stale_after_s: int = 60) -> int:
        """Re-queue 'running' jobs whose worker died (stale heartbeat)."""
        cutoff = _now() - timedelta(seconds=stale_after_s)
        n = 0
        with self.sf() as s:
            for j in s.query(Job).filter(Job.state == "running"):
                if j.heartbeat_at is None or j.heartbeat_at < cutoff:
                    j.state, n = "queued", n + 1
            s.commit()
        return n

    def cancel(self, job_id: int):
        with self.sf() as s:
            j = s.get(Job, job_id)
            if j and j.state in ("queued", "running"):
                j.state = "cancelled"
            s.commit()

    def run_next(self) -> bool:
        """Claim and run one queued job synchronously. False if none queued."""
        with self.sf() as s:
            j = s.query(Job).filter(Job.state == "queued").order_by(Job.id).first()
            if not j:
                return False
            j.state, j.attempts, j.heartbeat_at = "running", j.attempts + 1, _now()
            s.commit()
            jid = j.id
        ctx = JobContext(self.sf, jid)
        try:
            with self.sf() as s:
                job = s.get(Job, jid)
            self.handlers[job.kind](job, ctx)
            with self.sf() as s:
                j = s.get(Job, jid)
                if j.state == "running":
                    j.state, j.progress = "done", 1.0
                s.commit()
        except Exception as e:  # noqa: BLE001
            with self.sf() as s:
                j = s.get(Job, jid)
                j.error = repr(e)
                j.state = "queued" if j.attempts < self.max_attempts else "failed"
                s.commit()
        return True

    def drain(self):
        while self.run_next():
            pass

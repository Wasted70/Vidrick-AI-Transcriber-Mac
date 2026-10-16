"""One background transcription job with bounded, thread-safe UI events."""

from dataclasses import dataclass
import threading
from typing import Callable

from .transcription import (
    JobSpec, Progress, TranscriptResult, TranscriptionCancelled, TranscriptionCleanupError,
    TranscriptionError, run_transcription,
)


@dataclass(frozen=True)
class JobEvent:
    job_id: int
    kind: str
    payload: Progress | TranscriptResult | str | None = None


Backend = Callable[[JobSpec, threading.Event, Callable[[Progress], None]], TranscriptResult]


def _brief_error(error: BaseException) -> str:
    """Retain one short message, not potentially unbounded backend output."""
    try:
        message = " ".join(str(error).split())
    except Exception:
        message = ""
    message = message or type(error).__name__
    return message if len(message) <= 500 else message[:499] + "…"


class JobManager:
    """Run one job at a time without calling Tk or blocking for completion.

    Poll ``drain`` from the UI thread. The newest progress replaces earlier
    progress; a terminal event remains until drained and is exposed only after
    the worker has exited. A completed, undrained job also prevents restart so
    its result cannot be lost. ``cancel`` and completion publication share a
    lock: the first of those operations decides the outcome. A stop or cleanup
    failure remains visible even when the user requested cancellation.
    """

    def __init__(self, backend: Backend | None = None):
        self._backend = backend if backend is not None else run_transcription
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._job_id = 0
        self._progress: JobEvent | None = None
        self._terminal: JobEvent | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def cancelling(self) -> bool:
        with self._lock:
            return bool(self._thread is not None and self._thread.is_alive()
                        and self._cancel is not None and self._cancel.is_set())

    def start(self, spec: JobSpec) -> bool:
        with self._lock:
            if ((self._thread is not None and self._thread.is_alive())
                    or self._terminal is not None):
                return False
            self._job_id += 1
            job_id = self._job_id
            cancel = threading.Event()
            self._cancel = cancel
            self._progress = None
            try:
                self._thread = threading.Thread(
                    target=self._run, args=(job_id, spec, cancel),
                    name=f"VidrickTranscription-{job_id}", daemon=False)
                self._thread.start()
            except Exception as exc:
                self._thread = None
                self._cancel = None
                raise TranscriptionError(
                    f"Could not start transcription worker: {_brief_error(exc)}") from exc
            return True

    def cancel(self) -> bool:
        with self._lock:
            if (self._thread is None or not self._thread.is_alive()
                    or self._terminal is not None or self._cancel is None):
                return False
            self._cancel.set()
            self._progress = None
            return True

    def drain(self) -> list[JobEvent]:
        """Return available events without waiting for a worker or a process."""
        with self._lock:
            events = []
            if self._progress is not None:
                events.append(self._progress)
                self._progress = None
            exited = self._thread is None or not self._thread.is_alive()
            if self._terminal is not None and exited:
                events.append(self._terminal)
                self._terminal = None
                self._thread = None
                self._cancel = None
            return events

    def _emit(self, job_id: int, progress: Progress) -> None:
        with self._lock:
            if (job_id != self._job_id or self._terminal is not None
                    or self._cancel is None or self._cancel.is_set()):
                return
            self._progress = JobEvent(job_id, "progress", progress)

    def _run(self, job_id: int, spec: JobSpec, cancel: threading.Event) -> None:
        cleanup_failed = False
        try:
            if cancel.is_set():
                raise TranscriptionCancelled()
            result = self._backend(spec, cancel, lambda value: self._emit(job_id, value))
            kind, payload = "completed", result
        except TranscriptionCancelled:
            kind, payload = "cancelled", None
        except TranscriptionCleanupError as exc:
            cleanup_failed = True
            kind, payload = "failed", _brief_error(exc)
        except BaseException as exc:
            # Even a worker-only SystemExit must release the UI's active job.
            kind, payload = "failed", _brief_error(exc)
        with self._lock:
            if job_id != self._job_id:
                return
            if cancel.is_set() and not cleanup_failed:
                kind, payload = "cancelled", None
            self._terminal = JobEvent(job_id, kind, payload)

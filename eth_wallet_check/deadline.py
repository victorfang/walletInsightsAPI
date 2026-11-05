"""Shared, bounded capacity for work that must fit a request deadline."""

from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock


REQUEST_BUDGET_SECONDS = 2.6


class BoundedExecutor:
    """Reject excess work immediately instead of building an unbounded queue.

    Workers must not submit to this pool and then wait for another pool job.
    Deadlines limit how long callers wait; they cannot stop a running thread.
    """

    def __init__(self, max_workers=24, max_pending=0):
        if max_workers < 1 or max_pending < 0:
            raise ValueError("Executor capacity must be positive with nonnegative pending work")
        self._slots = BoundedSemaphore(max_workers + max_pending)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="wallet-source"
        )

    def submit(self, fn, *args, **kwargs):
        if not self._slots.acquire(blocking=False):
            return None
        try:
            future = self._executor.submit(fn, *args, **kwargs)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


_shared = None
_shared_lock = Lock()


def shared_executor():
    """Return a lazily created process-wide pool with no pending-job backlog."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = BoundedExecutor(max_workers=24, max_pending=0)
        return _shared

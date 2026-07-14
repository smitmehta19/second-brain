"""Background job queue for processing captures without blocking the bot."""

from __future__ import annotations

import asyncio
import logging

from src.models.schemas import PipelineResult, ProcessingStatus, RawCapture
from src.pipeline.processor import process_capture

logger = logging.getLogger(__name__)


class ProcessingQueue:
    """Async queue that fans out captures to a pool of worker tasks.

    Usage::

        queue = ProcessingQueue(max_workers=5)
        await queue.start()
        capture_id = await queue.enqueue(capture)
        # ... later ...
        result = await queue.get_result(capture_id)
        await queue.stop()
    """

    def __init__(self, max_workers: int = 5) -> None:
        self._queue: asyncio.Queue[RawCapture] = asyncio.Queue()
        self._results: dict[str, PipelineResult] = {}
        self._max_workers = max_workers
        self._running = False
        self._workers: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, capture: RawCapture) -> str:
        """Add a capture to the processing queue and return its ID."""
        await self._queue.put(capture)
        logger.debug("Enqueued capture %s (queue size: %d)", capture.id, self._queue.qsize())
        return capture.id

    async def get_result(self, capture_id: str) -> PipelineResult | None:
        """Return the processing result if available, otherwise ``None``."""
        return self._results.get(capture_id)

    async def start(self) -> None:
        """Spawn worker tasks to begin draining the queue."""
        if self._running:
            return
        self._running = True
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker(i), name=f"queue-worker-{i}")
            self._workers.append(task)
        logger.info("Processing queue started with %d workers", self._max_workers)

    async def stop(self) -> None:
        """Signal workers to stop and wait for them to finish current items."""
        if not self._running:
            return
        self._running = False

        # Push sentinel values so workers wake up and exit
        for _ in self._workers:
            await self._queue.put(None)  # type: ignore[arg-type]

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Processing queue stopped")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of captures waiting in the queue."""
        return self._queue.qsize()

    @property
    def processed_count(self) -> int:
        """Number of captures that have been processed (success or failure)."""
        return len(self._results)

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        """Process items from the queue until stopped."""
        logger.debug("Worker %d started", worker_id)
        while self._running:
            try:
                capture = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Sentinel check — ``None`` means shut down
            if capture is None:
                self._queue.task_done()
                break

            try:
                result = await process_capture(capture)
                self._results[capture.id] = result
            except Exception:  # noqa: BLE001
                logger.exception("Worker %d failed on capture %s", worker_id, capture.id)
                self._results[capture.id] = PipelineResult(
                    raw_id=capture.id,
                    status=ProcessingStatus.FAILED,
                    error="Worker-level failure",
                )
            finally:
                self._queue.task_done()

        logger.debug("Worker %d stopped", worker_id)

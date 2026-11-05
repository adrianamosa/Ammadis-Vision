"""POST event JSON to the configured webhook. Failures are retried, not fatal."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time

import httpx

logger = logging.getLogger("ammadis.webhook")


class WebhookPublisher:
    def __init__(
        self,
        url: str,
        timeout: float = 5.0,
        retries: int = 3,
        client: httpx.Client | None = None,
        backoff_seconds: float = 0.5,
    ) -> None:
        self.url = (url or "").strip()
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sent = 0
        self._failed = 0
        self._dropped = 0
        self._logged = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ammadis-webhook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._owns_client:
            self._client.close()

    def publish(self, event: dict) -> None:
        if not self.url:
            with self._lock:
                self._logged += 1
            logger.info(
                "webhook url empty; logged %s %s",
                event.get("event_type"),
                event.get("event_id"),
            )
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            logger.warning("webhook queue full; dropped %s", event.get("event_id"))

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": self._queue.qsize(),
                "sent": self._sent,
                "failed": self._failed,
                "dropped": self._dropped,
                "logged": self._logged,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if event is None:
                self._queue.task_done()
                return
            try:
                ok = self._deliver(event)
                with self._lock:
                    if ok:
                        self._sent += 1
                    else:
                        self._failed += 1
            finally:
                self._queue.task_done()

    def _deliver(self, event: dict) -> bool:
        body = json.dumps(event).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ammadis-vision-edge/0.1",
            "X-Ammadis-Event": str(event.get("event_type") or ""),
            "X-Ammadis-Event-Id": str(event.get("event_id") or ""),
        }
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                response = self._client.post(self.url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    return True
                logger.warning(
                    "webhook %s returned HTTP %s",
                    event.get("event_id"),
                    response.status_code,
                )
            except httpx.HTTPError as exc:
                logger.warning("webhook %s failed: %s", event.get("event_id"), exc)
            if attempt + 1 < attempts and not self._stop.is_set():
                time.sleep(self.backoff_seconds * (2**attempt))
        return False

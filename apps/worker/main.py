"""Standalone durable worker process for Awn tool calls."""

from __future__ import annotations

import logging
import signal
import threading

from awn.api.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    settings = app.state.settings
    worker = app.state.worker_service
    stopping = threading.Event()

    def stop(_: int, __: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    logging.info("Awn worker started")
    try:
        while not stopping.is_set():
            if not worker.run_once():
                stopping.wait(settings.worker_poll_seconds)
    finally:
        app.state.database.dispose()
        logging.info("Awn worker stopped")


if __name__ == "__main__":
    main()

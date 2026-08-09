"""Run the single fixed Phase-1 outbox dispatcher process."""
from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.db import get_engine, get_session_factory
from app.services.outbox_relay import OutboxDispatcher, notification_adapter_from_settings

logger = logging.getLogger("control_plane.outbox.worker")
_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    factory = get_session_factory(get_engine(settings.database_url))
    dispatcher = OutboxDispatcher(
        factory,
        settings,
        notification_adapter=notification_adapter_from_settings(settings),
        worker_id="worker:outbox-fixed-1",
    )
    logger.info("fixed outbox worker started")
    while _running:
        stats = dispatcher.dispatch_batch(limit=50)
        if stats["claimed"] == 0:
            time.sleep(max(0.05, settings.outbox_relay_interval_seconds))
    logger.info("fixed outbox worker stopped")


if __name__ == "__main__":
    main()

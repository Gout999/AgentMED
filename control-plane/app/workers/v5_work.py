"""Run the fixed V5 Work reaction dispatcher process."""
from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.db import get_engine, get_session_factory
from app.services.v5_work_dispatcher import WorkReactionRelay

logger = logging.getLogger("control_plane.v5_work_dispatcher.worker")
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
    relay = WorkReactionRelay(
        factory,
        worker_id="worker:v5-work-fixed-1",
        claim_ttl_seconds=settings.outbox_claim_ttl_seconds,
        max_delivery_attempts=settings.outbox_max_attempts,
        retry_initial_seconds=settings.outbox_retry_initial_seconds,
        retry_max_seconds=settings.outbox_retry_max_seconds,
    )
    logger.info("fixed V5 Work dispatcher started")
    while _running:
        stats = relay.dispatch_batch(limit=50)
        if stats["claimed"] == 0:
            time.sleep(max(0.05, settings.outbox_relay_interval_seconds))
    logger.info("fixed V5 Work dispatcher stopped")


if __name__ == "__main__":
    main()

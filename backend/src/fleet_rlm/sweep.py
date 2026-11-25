"""Out-of-band workspace cleanup that cron runs with `python -m fleet_rlm.sweep`."""

import asyncio
import json
import logging
from contextlib import closing
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from . import db as store
from .config import Settings
from .interpreter import (
    AsyncDaytona,
    DaytonaError,
    SandboxStartupError,
    delete_pending_sandbox,
    delete_sandbox_by_id,
    make_daytona_client,
)
from .storage import ObjectStorage, StorageError

_LOG = logging.getLogger(__name__)
_BATCH_SIZE = 100


@dataclass
class SweepReport:
    runs: int = 0
    sandboxes: int = 0
    objects: int = 0
    sessions: int = 0
    failures: int = 0


async def _reclaim(
    sessionmaker: store.SessionFactory, storage: ObjectStorage, entries: list[store.ObjectCleanup]
) -> tuple[int, int]:
    """Return reclaimed and failed counts. Forget only the objects that storage deleted."""
    reclaimed, failures = [], 0
    try:
        for entry in entries:
            try:
                await storage.reclaim(entry.object_key, entry.upload_id)
            except StorageError:
                failures += 1
            else:
                reclaimed.append(entry.object_key)
    finally:
        if reclaimed:
            await store.forget_discarded_objects(sessionmaker, reclaimed)
    return len(reclaimed), failures


async def reclaim_discarded_objects(
    sessionmaker: store.SessionFactory, storage: ObjectStorage, session_id: UUID
) -> None:
    entries = await store.list_discarded_objects(sessionmaker, session_id)
    if (await _reclaim(sessionmaker, storage, entries))[1]:
        raise StorageError("Some workspace objects could not be reclaimed")


async def purge_session(
    sessionmaker: store.SessionFactory,
    daytona: AsyncDaytona,
    settings: Settings,
    storage: ObjectStorage,
    session: store.Session,
) -> None:
    """Delete the external resources of a session marked `deleting`, then its row."""
    if session.daytona_sandbox_id:
        await delete_sandbox_by_id(daytona, session.daytona_sandbox_id)
    if session.pending_sandbox_name:
        await delete_pending_sandbox(daytona, settings, session.pending_sandbox_name)
    await reclaim_discarded_objects(sessionmaker, storage, session.id)
    await storage.delete_session(session.id)
    async with sessionmaker() as db, db.begin():
        row = await db.get(store.Session, session.id, with_for_update=True)
        if row is not None:
            await db.delete(row)


async def _retire_sandboxes(
    sessionmaker: store.SessionFactory, daytona: AsyncDaytona, report: SweepReport, limit: int
) -> None:
    after = None
    while batch := await store.list_retired_sandboxes(sessionmaker, after=after, limit=limit):
        for session_id, sandbox_id in batch:
            try:
                await delete_sandbox_by_id(daytona, sandbox_id)
            except DaytonaError as error:
                _LOG.warning("Could not retire VM %s (%s)", sandbox_id, type(error).__name__)
                report.failures += 1
                continue
            if await store.clear_retired_sandbox(sessionmaker, session_id, sandbox_id):
                report.sandboxes += 1
        after = batch[-1][0]


async def _reclaim_objects(
    sessionmaker: store.SessionFactory, storage: ObjectStorage, report: SweepReport, limit: int
) -> None:
    after = None
    while batch := await store.list_discarded_object_batch(sessionmaker, after=after, limit=limit):
        reclaimed, failures = await _reclaim(sessionmaker, storage, batch)
        if failures:
            _LOG.warning("%d workspace objects will retry", failures)
        report.objects += reclaimed
        report.failures += failures
        after = batch[-1].object_key


async def _finish_deletes(
    sessionmaker: store.SessionFactory,
    daytona: AsyncDaytona,
    settings: Settings,
    storage: ObjectStorage,
    report: SweepReport,
    limit: int,
) -> None:
    after = None
    while batch := await store.list_deleting_sessions(sessionmaker, after=after, limit=limit):
        for session in batch:
            try:
                await purge_session(sessionmaker, daytona, settings, storage, session)
            except (DaytonaError, SandboxStartupError, StorageError, SQLAlchemyError) as error:
                _LOG.warning("Session %s delete will retry (%s)", session.id, type(error).__name__)
                report.failures += 1
            else:
                report.sessions += 1
        after = batch[-1].id


async def run_sweep(
    settings: Settings,
    sessionmaker: store.SessionFactory,
    daytona: AsyncDaytona,
    storage: ObjectStorage,
    *,
    batch_size: int = _BATCH_SIZE,
) -> SweepReport:
    """Run one idempotent pass. Reaped runs can flag sandboxes and release objects."""
    report = SweepReport()
    report.runs = await store.reap_stale_runs(sessionmaker, settings.stale_run_timeout_seconds)
    await _retire_sandboxes(sessionmaker, daytona, report, batch_size)
    await _reclaim_objects(sessionmaker, storage, report, batch_size)
    await _finish_deletes(sessionmaker, daytona, settings, storage, report, batch_size)
    return report


async def _main() -> SweepReport:
    settings = Settings()
    engine, sessionmaker = store.create_db(settings)
    try:
        with closing(ObjectStorage(settings)) as storage:
            async with make_daytona_client(settings) as daytona:
                return await run_sweep(settings, sessionmaker, daytona, storage)
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    report = asyncio.run(_main())
    print(json.dumps(asdict(report)))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

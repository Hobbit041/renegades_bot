from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bot.domain import initial_state, sync_population


class Store:
    """Single-camp aggregate, atomic SQLite transactions and durable delivery queue."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine("sqlite+aiosqlite:///" + str(path), connect_args={"timeout": 30})
        self.lock = asyncio.Lock()

    async def init(self, rules):
        async with self.engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS campaign (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)"))
            await conn.execute(text("INSERT OR IGNORE INTO campaign(id,value) VALUES(1,:v)"),
                               {"v": json.dumps(initial_state(rules), ensure_ascii=False)})
        async with self.transaction():
            pass

    @asynccontextmanager
    async def transaction(self):
        async with self.lock, self.engine.connect() as conn:
            await conn.execute(text("BEGIN IMMEDIATE"))
            try:
                state = json.loads((await conn.execute(text("SELECT value FROM campaign WHERE id=1"))).scalar_one())
                sync_population(state)
                yield state
                sync_population(state)
                await conn.execute(text("UPDATE campaign SET value=:v WHERE id=1"), {"v": json.dumps(state, ensure_ascii=False, allow_nan=False)})
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def snapshot(self):
        async with self.lock, self.engine.connect() as conn:
            return json.loads((await conn.execute(text("SELECT value FROM campaign WHERE id=1"))).scalar_one())

    async def close(self):
        await self.engine.dispose()

"""Open short-lived PostgreSQL transactions for the API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
        if not self._dsn:
            raise psycopg.OperationalError("Database connection is not configured.")
        async with await psycopg.AsyncConnection.connect(
            self._dsn,
            connect_timeout=5,
            prepare_threshold=None,
            row_factory=dict_row,
        ) as connection:
            yield connection

    async def check(self) -> bool:
        try:
            async with asyncio.timeout(5):
                async with self.connection() as connection:
                    await connection.execute("SELECT 1")
        except (psycopg.Error, TimeoutError):
            return False
        return True

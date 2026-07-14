"""Database manager for SQLite connections.

Provides async SQLite database access via aiosqlite. Handles:
- Connection lifecycle (open / close).
- Automatic table creation from migration scripts.
- Safe connection management with context managers.

Sprint 1 - Prompt 3.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database connections and schema initialization.

    Usage::

        db = DatabaseManager("data/liz_memory.db")
        await db.initialize()

        async with db.connection() as conn:
            await conn.execute("INSERT INTO ...")

        await db.close()
    """

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        self._connection: aiosqlite.Connection | None = None
        logger.info("DatabaseManager created (path=%s)", database_path)

    @property
    def database_path(self) -> str:
        """Return the configured database file path."""
        return self._database_path

    @property
    def is_initialized(self) -> bool:
        """Whether the database has been initialized."""
        return self._connection is not None

    async def initialize(self) -> None:
        """Initialize the database connection and create tables.

        Ensures the parent directory exists, opens an aiosqlite
        connection, and runs the initial migration SQL to create
        the conversations table and indexes.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._connection is not None:
            logger.debug("DatabaseManager already initialized")
            return

        # Ensure the parent directory exists.
        db_file = Path(self._database_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        # Open connection.
        self._connection = await aiosqlite.connect(self._database_path)
        # Enable row factory for dict-like access.
        self._connection.row_factory = aiosqlite.Row
        # Enable WAL mode for better concurrent read performance.
        await self._connection.execute("PRAGMA journal_mode = WAL")
        await self._connection.execute("PRAGMA foreign_keys = ON")

        # Run initial migration.
        await self._run_migration("initial.sql")

        logger.info(
            "DatabaseManager initialized (db=%s)", self._database_path
        )

    async def connection(self) -> aiosqlite.Connection:
        """Return the active database connection.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        if self._connection is None:
            raise RuntimeError(
                "DatabaseManager not initialized. Call initialize() first."
            )
        return self._connection

    async def close(self) -> None:
        """Close the database connection safely.

        Safe to call multiple times; subsequent calls are no-ops.
        Commits any pending transaction before closing.
        """
        if self._connection is None:
            return

        try:
            await self._connection.commit()
            await self._connection.close()
            logger.info("DatabaseManager connection closed")
        except Exception:
            logger.exception("Error closing database connection")
        finally:
            self._connection = None

    async def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute a SQL statement and return the cursor.

        Convenience method for simple one-off queries.

        Args:
            sql:        The SQL statement to execute.
            parameters: Query parameters (positional).

        Returns:
            The aiosqlite Cursor.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        conn = await self.connection()
        return await conn.execute(sql, parameters)

    async def execute_script(self, sql_script: str) -> None:
        """Execute a multi-statement SQL script.

        Args:
            sql_script: Raw SQL with multiple statements.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        conn = await self.connection()
        await conn.executescript(sql_script)

    async def run_migration(self, migration_name: str) -> None:
        """Load and execute a migration SQL file.

        Public method so external code can trigger
        additional migrations after the initial one.

        Args:
            migration_name: Filename of the migration.
        """
        migrations_dir = Path(__file__).parent / "migrations"
        migration_file = migrations_dir / migration_name

        if not migration_file.exists():
            logger.warning("Migration file not found: %s", migration_name)
            return

        try:
            sql = migration_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Cannot read migration file {migration_file}: {exc}"
            ) from exc

        try:
            await self.execute_script(sql)
        except Exception as exc:
            raise RuntimeError(
                f"Migration {migration_name} failed: {exc}"
            ) from exc
        logger.info("Migration applied: %s", migration_name)

    async def _run_migration(self, migration_name: str) -> None:
        """Internal migration runner used during initialize()."""
        await self.run_migration(migration_name)

-- Initial migration for Liz Coder Plus memory system.
-- Sprint 1 - Prompt 3.

-- Enable WAL mode for better concurrent read performance.
PRAGMA journal_mode = WAL;

-- conversations table: stores all messages from all sessions.
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata    TEXT    DEFAULT '{}'
);

-- Index for fast session history lookups.
CREATE INDEX IF NOT EXISTS idx_conversations_session_id
    ON conversations (session_id);

-- Index for timestamp-based queries.
CREATE INDEX IF NOT EXISTS idx_conversations_created_at
    ON conversations (session_id, created_at);
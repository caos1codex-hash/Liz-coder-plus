-- Sprint 3.4 — Persistence for Planner plans and their steps.
--
-- This migration adds two tables:
--   plans        — top-level TaskPlan (id, goal, status, priority, timestamps, metadata)
--   plan_steps   — one row per PlanStep inside a plan (FK → plans(id) ON DELETE CASCADE)
--
-- Together with the existing execution_history table (Sprint 1.8), this
-- gives the PlannerExecutor full auditability: every run can be replayed
-- by joining plans → plan_steps → execution_history (on workflow_id +
-- metadata.plan_id).
--
-- Design notes:
--  * `plans.priority` uses the planner's enum values
--    ('low','normal','high','critical') which match multiagent.Priority.
--  * `plans.status` uses the planner's PlanStatus values
--    ('not_started','planning','ready','running','completed','failed','cancelled').
--  * `plan_steps.status` uses the planner's StepStatus values
--    ('pending','ready','running','success','failed','skipped').
--  * All JSON-shaped columns are stored as TEXT and serialised by the
--    repository layer (consistent with the existing tasks/workflows tables).
--  * All timestamps are ISO-8601 UTC strings.

-- ============================================================
-- plans table
-- ============================================================
CREATE TABLE IF NOT EXISTS plans (
    id              TEXT PRIMARY KEY,
    goal            TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    priority        TEXT    NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low', 'normal', 'high', 'critical')),
    status          TEXT    NOT NULL DEFAULT 'not_started'
                    CHECK(status IN ('not_started', 'planning', 'ready',
                                     'running', 'completed', 'failed',
                                     'cancelled')),
    metadata        TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    workflow_id     TEXT,        -- last workflow_id the plan ran as (if any)
    agent_context_summary TEXT NOT NULL DEFAULT '{}'  -- snapshot of registry at plan time
);

CREATE INDEX IF NOT EXISTS idx_plans_status
    ON plans (status);

CREATE INDEX IF NOT EXISTS idx_plans_priority
    ON plans (priority);

CREATE INDEX IF NOT EXISTS idx_plans_workflow_id
    ON plans (workflow_id);

CREATE INDEX IF NOT EXISTS idx_plans_created_at
    ON plans (created_at);

-- ============================================================
-- plan_steps table
-- ============================================================
CREATE TABLE IF NOT EXISTS plan_steps (
    id                  TEXT PRIMARY KEY,
    plan_id             TEXT    NOT NULL,
    title               TEXT    NOT NULL DEFAULT '',
    description         TEXT    NOT NULL DEFAULT '',
    estimated_duration  REAL    NOT NULL DEFAULT 0.0,
    dependencies        TEXT    NOT NULL DEFAULT '[]',   -- JSON array of step ids
    required_agents     TEXT    NOT NULL DEFAULT '[]',   -- JSON array of agent names
    status              TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'ready', 'running',
                                         'success', 'failed', 'skipped')),
    metadata            TEXT    NOT NULL DEFAULT '{}',    -- includes agent_assignment + execution
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,

    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_id
    ON plan_steps (plan_id);

CREATE INDEX IF NOT EXISTS idx_plan_steps_status
    ON plan_steps (status);

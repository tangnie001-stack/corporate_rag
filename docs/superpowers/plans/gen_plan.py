plan = """---
change: session-history
design-doc: docs/superpowers/specs/2026-06-27-session-history-design.md
base-ref: 574681cbe6aaf7c3cd881ce7503b296c16b0f974
---

# Session History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session history management (CRUD) with MySQL persistence and sidebar UI, enabling users to view, switch, and delete conversation sessions.

**Architecture:** Cold-hot separation pattern: MySQL stores full session/message history (cold storage), Redis stores current session RAG context (hot storage). New `sessions` table added; `conversation_history` table's FK on `kb_id` removed. Backend exposes three new REST endpoints. Frontend sidebar replaces knowledge base list with session list.

**Tech Stack:** FastAPI, MySQL 8.0 (PyMySQL), Redis (async via asyncio.to_thread), SSE streaming, HTML/Tailwind CSS/vanilla JS

## Global Constraints

- No FK constraints on `kb_id` in sessions/conversation_history tables
- Session IDs follow existing format: `session_<timestamp>_<random>`
- Session title = first 20 chars of first user message, written once
- `INSERT_SESSION` uses `ON DUPLICATE KEY UPDATE` for idempotency
- MySQL async writes use `asyncio.to_thread()` -- never block the event loop
- All exceptions in async persistence are caught, logged, and swallowed -- never propagate to SSE response
- Delete session endpoint must cascade: Redis key -> MySQL sessions -> MySQL conversation_history
- Ruff check + pytest must pass before any commit

---
"""
print("written so far: " + str(len(plan)))

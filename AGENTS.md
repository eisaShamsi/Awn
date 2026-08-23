# Awn development entry point

This `AGENTS.md` file is the primary, automatically discovered entry point for
developing Awn with Codex. Keep it concise and use it to route work to the
authoritative project documents instead of duplicating their contents here.

## Required context

Before starting work in this repository:

1. Read `docs/STANDING_ORDERS.md` and comply with every active order.
2. Read the latest entry in `docs/MOCH.md` to recover the current project context.
3. Use the following sources of truth for the subject being changed:
   - `PRODUCT.md` for product vision, scope, and requirements.
   - `ARCHITECTURE.md` and `docs/adr/` for architecture and accepted technical decisions.
   - `ROADMAP.md` for delivery stages, priorities, and completion criteria.
   - `docs/USER_GUIDE.md` for user-facing operation and troubleshooting.

## Precedence

1. The user's latest explicit instruction and the active standing orders take priority.
2. Accepted ADRs govern technical decisions until superseded by a newer ADR.
3. Product, architecture, roadmap, and user documentation govern their respective scopes.
4. When sources conflict, record the resolution in `docs/MOCH.md` and update the
   authoritative source instead of leaving contradictory guidance.

## Maintenance

After each material user exchange or completed body of work:

1. Update the current session in `docs/MOCH.md` with the topic, decisions, actions, results, and open work.
2. Update `docs/STANDING_ORDERS.md` only when the user explicitly adds, changes, or withdraws a standing instruction.
3. Never place passwords, API keys, credentials, or unnecessary sensitive data in either file.
4. Preserve history: append corrections and status changes instead of silently rewriting prior decisions.

Nested `AGENTS.md` files may add implementation-specific instructions within their own scope.

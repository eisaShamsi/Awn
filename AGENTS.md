# Awn development entry point

This `AGENTS.md` file is the primary, automatically discovered entry point for
developing Awn with Codex. Keep it concise and use it to route work to the
authoritative project documents instead of duplicating their contents here.

## Required context

Before starting work in this repository:

1. Read `docs/STANDING_ORDERS.md` and comply with every active order.
2. Read the latest entry in `docs/MOCH.md` to recover the current project context.
3. Use the following sources of truth for the subject being changed:
   - `docs/FUNCTION_CONCEPTS.md` for the owner-approved purpose and boundaries of each function.
   - `docs/AUDIT_COUNCIL.md` for the mandatory independent review gate.
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

## Mandatory function gate

Before designing or implementing a new function or a material change to an existing one:

1. Identify the applicable Function Concept ID and exact `APPROVED` version.
2. If none exists, draft one from `docs/concepts/TEMPLATE.md` and stop before design until the owner personally approves that version.
3. Derive the design, acceptance criteria, implementation, and evidence from the approved concept without silently expanding it.
4. Invoke `$awn-audit-council` for independent review of each material phase and before handoff.
5. Do not tell the owner a build is ready for trial unless the council report says `READY_FOR_OWNER_TRIAL`.

The implementer cannot approve a Function Concept, impersonate a missing council member, or self-certify the build.

## Maintenance

After each material user exchange or completed body of work:

1. Update the current session in `docs/MOCH.md` with the topic, decisions, actions, results, and open work.
2. Update `docs/STANDING_ORDERS.md` only when the user explicitly adds, changes, or withdraws a standing instruction.
3. Never place passwords, API keys, credentials, or unnecessary sensitive data in either file.
4. Preserve history: append corrections and status changes instead of silently rewriting prior decisions.
5. Maintain the Function Concept register and retain council reports under `docs/audits/`.

Nested `AGENTS.md` files may add implementation-specific instructions within their own scope.

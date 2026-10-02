# Workspace Agent Rules

## MANDATORY BEHAVIORAL RULE: EXACT USER COMMAND EXECUTION

1. **NO UNREQUESTED SUBSTITUTIONS:** Execute ONLY what the user explicitly asks for. Never infer, substitute, or replace requested models, settings, or parameters on your own.
2. **NO UNREQUESTED DB RESETS OR DELETIONS:** Never reset databases, delete tables, or alter core state unless explicitly instructed by the user in the prompt.
3. **STRICT LOG & DATA QUALITY VERIFICATION:** When asked to check, perform full end-to-end inspection (runner status, log tracebacks, and non-null DB column contents).

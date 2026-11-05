# Upstream Gateway Project Rules

These rules apply to the entire repository.

## Read before editing

- Read `README.md`, `docs/CHANGELOG.md`, and documentation relevant to the current task, then inspect the affected code, callers, tests, and configuration before choosing a change.
- Read `docs/ROADMAP.md` for planning tasks. Treat roadmap items as proposals, not permission to implement them; verify claims about current behavior against code and tests.
- Do not invent endpoints, schema fields, provider behavior, or infrastructure outside the requested scope. Ask when missing requirements would materially change the implementation; use judgment for routine details.
- Keep README and API documentation consistent with implemented behavior. Preserve unrelated user changes and existing environment files.
- For every task that adds, modifies, or deletes repository files, update `docs/CHANGELOG.md` under `Unreleased` in the same change. This includes code, tests, configuration, and documentation-only changes.
- Record one concise entry per logical change under `Added`, `Changed`, `Fixed`, or `Removed`, as appropriate; do not record every save or duplicate an entry for the changelog update itself. Preserve existing entries and release history.

## Codebase Memory MCP

- Use the `codebase-memory` skill when available. Prioritize Codebase Memory MCP for code discovery, source reading, architecture questions, and caller/callee tracing over flat text searches.
- Discover the available tools and their current schemas; do not copy assumed parameters from another project or guess tool names.
- Check project/index status with `list_projects` and/or `index_status`. If the repository is not indexed, use `index_repository` for this repository before graph queries.
- Use `search_graph` to locate symbols, then `get_code_snippet` with the discovered qualified name to read them. Use `trace_path` for callers/callees, `get_architecture` for module structure, and `query_graph` for complex graph questions.
- Use `search_code` for text patterns and `get_graph_schema` when graph labels or relationships are unclear. Follow the returned pagination fields rather than assuming results are complete.
- After source changes, use `detect_changes` and refresh the index as needed. Verify affected source before editing if graph results are stale or incomplete.
- If the MCP is unavailable, disconnected, or returns no useful results, use dedicated grep/glob/read tools. Do not block the task or claim MCP results you did not obtain.
- Use standard tools for documentation/configuration reads, edits, builds, and tests. Keep generated index artifacts out of commits and Docker images.

## Ponytail and implementation scope

- Use the `ponytail` skill in **full** mode by default for coding tasks, unless the user explicitly selects another level or disables it. If the skill is unavailable, follow the smallest-correct-change rules below.
- Reuse existing code, then the standard library or installed dependencies before writing new code. Add no speculative abstractions, wrappers, configuration, or dependencies.
- Fix shared root causes rather than patching individual callers. Complete all affected callers, tests, fixtures, and documentation without unrelated refactoring.
- Preserve validation, security, cancellation handling, and error handling that prevents data loss. Comment only non-obvious reasons.
- Mark deliberate limitations with `shortcut: <the limit>, <when to upgrade>`, matching the existing code and ponytail convention.
- Add a small automated test for non-trivial behavior and regressions. Finish responses with what was skipped or unverified and any remaining risk.

## Gateway architecture and safety

- Keep application code in the existing `app/` structure: `api/`, `clients/`, `core/`, `db/`, `schemas/`, and `services/`. Tests belong in `tests/`; supporting documentation belongs in `docs/`.
- Preserve the application factory and async lifespan in `app/main.py`, the shared HTTPX client, and explicit startup/shutdown cleanup. Run synchronous database operations in the existing thread pool, not on the event loop.
- Run **one Uvicorn worker**. Router statistics, cooldowns, and concurrency limits are process-local; do not add workers or replicas without an explicitly requested shared-state design.
- Preserve bounded attempts, capacity release, and conservative retry behavior. Timeouts and upstream failures may follow real side effects; request IDs are not idempotency guarantees.
- Use HTTPX `MockTransport`, synthetic payloads, and temporary SQLite databases for tests. Never contact live providers or send a valid live process request without explicit authorization.
- Keep health probes independent of upstream provider calls and business writes. `/health` is liveness; `/ready` checks initialized local dependencies and database accessibility.
- PostgreSQL is the runtime database, with schema owned by Alembic; SQLite is used only for tests. While no real data exists, the initial migration may be edited in place instead of adding a revision; once real data exists, create new revisions. Never delete local databases, prune persistent volumes, or run `docker compose down -v` without explicit approval.
- Settings are validated by `Settings.from_env()` from process variables; do not assume automatic dotenv loading. Compose loads runtime variables with `env_file: .env`.
- Keep `.env` untracked and excluded from Docker; keep `.env.example` tracked, complete, and free of actual secrets. Do not overwrite, rename, or delete existing environment files without approval.
- Do not log payloads, credentials, or raw internal errors. Treat persisted request/provider data as sensitive. Do not add Redis, Kubernetes, or other infrastructure unless requested.

## Python environment and verification

- Use Python 3.14+ and the repository's `.venv` for host commands; do not use global `python`, `pip`, or `pytest`. Inside Docker, use the image's Python interpreter.
- If `.venv` is missing, follow README setup instructions; do not install dependencies globally or report global-interpreter failures as project test failures.
- Run commands from the repository root. On Windows, use explicit PowerShell commands:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

- On Linux/macOS, use `.venv/bin/python` for the equivalent commands. Focused tests must use the same project interpreter.
- Run checks appropriate to the change. For Docker changes, validate Compose configuration, build the image, and check startup/health; verify persistence when storage behavior changes.
- For documentation-only changes, review referenced paths/commands and run `git diff --check`; application tests or Docker rebuilds are unnecessary unless runtime behavior also changed.
- Report commands, results, and blocked/skipped checks accurately. Never claim a build, test, endpoint check, or MCP query passed unless you executed it.

## Conventional commit messages

- Every commit must use Conventional Commits: `<type>(<scope>): <description>`.
- Allowed types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `build`, `ci`, `perf`, `style`.
- Choose a specific scope from the actual changes, such as `api`, `db`, `router`, `providers`, `config`, `docker`, `tests`, `docs`, `agents`, or `git`; avoid `misc`, `stuff`, `changes`, or `general`.
- Use `feat` for new behavior, `fix` for corrections, `refactor` for restructuring without behavior changes, `test` for tests, `docs` for documentation, and `build` for dependencies or packaging.
- Write a concise, imperative description starting with a lowercase letter and without a final period. Mark breaking changes with `!` before the colon and explain them in a `BREAKING CHANGE:` footer.
- Before committing, inspect the staged diff and derive type/scope from the actual changes. Stage only intended files and split unrelated concerns when practical.
- Do not create commits, push, or rewrite history unless requested. Never discard unrelated user changes.
- Valid examples: `feat(api): add readiness endpoint`, `fix(docker): persist SQLite WAL files`, `docs(agents): add repository rules`.
- Invalid examples: `Update files`, `fix: changes`, `docs(misc): update stuff`.

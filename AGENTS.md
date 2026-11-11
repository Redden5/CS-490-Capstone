# Project context

The Marshall University Intelligent Course Advisor Assistant is a CS 490 capstone. It helps students plan courses using their reported coursework and catalog-year evidence. Its degree audit is unofficial.

## Before development

- Read [project context](docs/project_context.md) and [development status](docs/development_status.md) before beginning any development task. The first explains the system and its decisions. The second identifies the verified milestone, open questions, and next action.
- Check the active branch and working tree with `git status --short --branch` before editing. Preserve existing changes, untracked files, and ignored local files. Stay within the requested scope and avoid unrelated refactoring.
- Read the component code, tests, and relevant guide. For planning consumers, use [the handoff contract](docs/planning_handoff.md) and [the API guide](docs/planning_api.md). For prerequisite data work, start with [the pipeline guide](backend/scripts/PREREQUISITE_PIPELINE.md).

For frontend database work, read `docs/database/README.md` and the user-provided schema snapshots in `docs/database/columns.csv` and `docs/database/constraints.csv`. They capture the public schema supplied on 2026-11-08; do not assume they include RLS policies, auth tables, or subsequent database changes.

## Implementation and verification

- Keep academic decisions in the shared backend planning service and its component evaluators. Both the CLI and HTTP route use that service. Preserve existing handoff versions, choice groups, provenance, and incomplete-evidence diagnostics.
- Keep planning database reads SELECT-only and privileged Supabase credentials server-side. Treat migrations and imports as separate operations requiring explicit authorization. Never commit environment files (including `.env` and `.env.local`) or expose credentials. Keep credentials and student coursework out of logs and documentation.
- Respect team responsibilities for catalog data, degree planning, frontend, scheduling, and advising. Coordinate changes to shared contracts with affected teammates before changing their consumers.
- After implementing changes, run the relevant tests. For planning, prerequisite, or API changes, run the offline suite from the repository root: `.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py"`. For frontend changes, run `npm run lint` and `npm run build` from `frontend/`. Record what actually ran and any failures or gaps.
- Never commit, push, or merge without explicit user authorization.

## Maintaining the handoff

- Preserve other teammates' work and reported development status in shared documentation. Update only the affected entries, and verify changes to another contributor's milestone or open issue before revising it.
- After each substantial implementation, update `docs/development_status.md` in the same task. Refresh the current state and next action. Add a short dated entry with affected components, decisions, verification results, limitations, and follow-up work. Consolidate superseded progress reports.
- Update `docs/project_context.md` when architecture, major functionality, or an important technical decision changes. Preserve decisions that still apply, edit only the affected sections, and link to the detailed component contract.
- Describe implemented behavior only after verifying it against code, tests, or committed artifacts. Label proposals, reported results, and unresolved work explicitly. Leave both context documents alone during read-only inspections unless the user requests an update.

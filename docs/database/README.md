# Database context

User-provided public Supabase/PostgreSQL schema exports, received 2026-11-08.
These describe the supplied snapshot, not a live database inspection or executable migration.

- `columns.csv`: columns, PostgreSQL types, nullability, defaults.
- `constraints.csv`: primary keys, foreign keys, unique constraints, checks, delete behavior.

## Frontend implications

- Table identifiers are lowercase. The course browser correctly targets `course` and its `min_credit_hours` / `max_credit_hours` columns. The Python seeder's `Course` table and `credit_hours` field are inconsistent with this snapshot.
- Catalog: `department`, `course`, `attribute`, `courseattribute`, `catalogyear`.
- Scheduling: `term` → `coursesection` → `sectionmeeting`; `sectionattribute` associates section attributes.
- Prerequisites/corequisites: `courserule` → `ruleclause` → `rulecourse`, which references required courses. Allowed rule types are PREREQUISITE and COREQUISITE. Clause AND/OR semantics are not established by these exports; confirm before implementing eligibility logic.
- Degree audits: `program` → `degreetemplate` (unique by program/catalog year) → hierarchical `degreerequirement`. Requirement types are ALL, CHOOSE_N, MIN_CREDITS; requirements link to courses and attributes through `requirementcourse` and `requirementattribute`.
- Source material: `sourcedocument` → `documentchunk`, with catalog year references and JSON metadata.
- No student profile, completed coursework, saved plan, or advisor conversation tables appear in the supplied public schema. Do not invent persistence contracts for these features.
- Exports do not establish row-level security policies, grants, triggers, functions, identity/sequence generation, views, or auth schema details. A null default on an ID does not prove it lacks identity generation. Verify these separately when needed.

## Tables

- `attribute`
- `catalogyear`
- `course`
- `courseattribute`
- `courserule`
- `coursesection`
- `degreerequirement`
- `degreetemplate`
- `department`
- `documentchunk`
- `program`
- `requirementattribute`
- `requirementcourse`
- `ruleclause`
- `rulecourse`
- `sectionattribute`
- `sectionmeeting`
- `sourcedocument`
- `term`

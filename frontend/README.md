# React + Vite

## Requirements

Before running the frontend, install:

- Git
- Node.js LTS 
    - macOS → .pkg
    - Windows → .msi

Check installation:

node -v
npm -v


## Setup

From the project root, go to the frontend folder:

cd frontend


Install dependencies:

npm install


Start the frontend:

npm run dev


Then open the local address shown in the terminal, usually:

http://localhost:5173

## Academic Info and Degree Audit

Start the existing FastAPI service in a second terminal using the
[backend setup instructions](../docs/planning_api.md#run-locally). The frontend
loads supported program/year combinations and sends reported coursework to:

- `GET /api/v1/planning/programs`
- `POST /api/v1/planning/evaluate`

Vite development uses `http://127.0.0.1:8000` by default. To use another API
origin, set `VITE_PLANNING_API_URL` in your untracked frontend environment file
and restart Vite. Supply the origin only, without `/api/v1/planning`.
Production builds default to same-origin `/api/v1/planning` paths unless this
variable is set at build time. The existing backend permits browser requests
from `localhost:5173` and `127.0.0.1:5173`; other origins need a separate backend
deployment change. The API currently supports unauthenticated local development,
so this frontend integration does not complete production authentication.

Existing sign-in and course search still require `VITE_SUPABASE_URL` and
`VITE_SUPABASE_PUBLISHABLE_KEY`. Never put a privileged Supabase key in a `VITE_`
variable. Do not commit environment files.

After signing in, open Academic Info from the Dashboard or the Degree Audit's
**Edit Academic Info** link. Select a program, search for completed courses or
enter their codes manually, optionally add grades/credits, and evaluate.
Individual successful attempts are preserved. Missing grades, unsupported grade
formats and unresolved equivalencies can require review.

Coursework and the last successful report remain in React memory during
navigation. Refresh, sign-out, or an account change clears them. Editing input
cancels an in-flight evaluation and marks the previous report as outdated.
There are no student-record database writes or browser-storage persistence.
The audit displays the backend v2.1 report, including choice bundles, uncertain
prerequisites and incomplete course-option coverage. It does not calculate a
degree-completion percentage or allocate credits between requirements.

## Verification

Run from the repository root:

```sh
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

The Node tests cover request construction, response/error handling, source-link
validation and preservation of grouped requirements. Live catalog/API checks
also require the separately configured backend and Supabase access.


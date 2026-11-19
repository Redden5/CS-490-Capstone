import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import CompletedCoursework from "../components/CompletedCoursework.jsx";
import { usePlanning } from "../context/planningContext.js";
import { loadPlanner } from "../lib/studentPlannerApi.js";
import "../styles/Dashboard.css";

const creditFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const percentFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, style: "percent" });

function DashboardLoading({ children }) {
  return (
    <div role="status">
      <span className="mu:sr-only">{children}</span>
      <div className="mu:grid mu:gap-4 mu:pt-2 mu:motion-safe:animate-pulse" aria-hidden="true">
        <span className="mu:h-4 mu:w-3/4 mu:rounded-sm mu:bg-mint" />
        <span className="mu:h-3 mu:w-1/2 mu:rounded-sm mu:bg-mint" />
        <span className="mu:h-3 mu:w-2/3 mu:rounded-sm mu:bg-mint" />
      </div>
    </div>
  );
}

function DashboardLoadError({ message, action, onRetry }) {
  return (
    <div className="mu:rounded-xl mu:border mu:border-line mu:bg-canvas mu:p-4">
      <p role="alert" className="mu:m-0 mu:mb-3 mu:text-sm mu:text-ink">{message}</p>
      <button type="button" className="mu:student-focus mu:min-h-11 mu:cursor-pointer mu:rounded-lg mu:border mu:border-herd mu:bg-paper mu:px-3 mu:font-campus mu:text-sm mu:font-semibold mu:text-herd mu:motion-safe:transition-colors mu:hover:bg-mint"
        onClick={(event) => {
          // Keep keyboard focus inside the card as the retry button gives way to the loading state.
          event.currentTarget.closest("section")?.focus();
          onRetry();
        }}>{action}</button>
    </div>
  );
}

function DegreeProgress({ record, evaluation, audit, requestError }) {
  if (record.loading) return <DashboardLoading>Loading academic information…</DashboardLoading>;
  if (record.error) return <p className="mu:m-0 mu:text-sm mu:text-muted">Degree progress is unavailable while coursework can’t be loaded.</p>;
  if (!record.revision) return <p className="mu:m-0 mu:text-sm mu:text-muted">Save a program in Academic Info to start your degree audit.</p>;

  const { program, rows } = record.input;
  const target = program.required_credits;
  const evidence = audit?.distinct_course_credit_evidence;
  const unknownCount = evidence?.courses_without_known_credits?.length ?? 0;
  const credits = rows.length ? evidence?.known_credit_subtotal : 0;
  const hasTarget = Number.isFinite(target) && target > 0;
  const hasCredits = hasTarget && Number.isFinite(credits) && credits >= 0;
  const filled = hasCredits ? Math.min(credits, target) : 0;

  return (
    <>
      <div className="mu:mb-4 mu:flex mu:flex-wrap mu:items-baseline mu:justify-between mu:gap-x-3 mu:gap-y-1">
        <h3 className="mu:m-0 mu:text-sm mu:font-semibold mu:break-words">{program.program_title ?? program.program_key}</h3>
        <span className="mu:text-xs mu:whitespace-nowrap mu:text-muted">Catalog {program.catalog_year}</span>
      </div>
      {hasCredits ? (
        <div className="mu:rounded-xl mu:border mu:border-line mu:bg-audit-tint mu:p-4">
          <p className="mu:m-0 mu:text-xs mu:font-semibold mu:text-muted">Estimated completed credits</p>
          <div className="mu:mt-1 mu:mb-3 mu:flex mu:flex-wrap mu:items-baseline mu:justify-between mu:gap-x-3">
            <div className="mu:flex mu:items-baseline mu:gap-1.5">
              <strong className="mu:text-4xl mu:leading-none mu:font-semibold mu:tracking-tight mu:tabular-nums">{creditFormat.format(credits)}</strong>
              <span className="mu:text-sm mu:text-muted">/ {creditFormat.format(target)} credits</span>
            </div>
            <span className="mu:text-sm mu:font-semibold mu:text-herd mu:tabular-nums">{percentFormat.format(filled / target)}</span>
          </div>
          <progress className="dashboard-credit-track mu:block mu:h-2.5 mu:w-full mu:appearance-none mu:overflow-hidden mu:rounded-full mu:border-0"
            aria-label="Estimated credits toward the degree" max={target} value={filled}
            aria-valuetext={`${creditFormat.format(credits)} of ${creditFormat.format(target)} credits`} />
          <p className="mu:m-0 mu:mt-3 mu:text-xs mu:leading-relaxed mu:text-muted">
            Estimated from saved coursework.
            {unknownCount > 0 && ` Credits unknown for ${unknownCount} ${unknownCount === 1 ? "course" : "courses"}.`}
          </p>
        </div>
      ) : hasTarget && evaluation.loading ? (
        <DashboardLoading>Checking saved course credits…</DashboardLoading>
      ) : (
        <p className="mu:m-0 mu:text-sm mu:text-muted">
          {!hasTarget ? "The credit target is unavailable for this program."
            : requestError || audit ? "Credit totals are unavailable. Open Degree Audit to check your coursework."
            : "Open Degree Audit to check credit progress for your saved coursework."}
        </p>
      )}
    </>
  );
}

function SavedSemesters({ semesters }) {
  if (!semesters.length) return <p className="mu:m-0 mu:text-sm mu:text-muted">No semester plans saved yet.</p>;

  return (
    <ol className="dashboard-scroll mu:student-focus mu:m-0 mu:max-h-record-list mu:list-none mu:divide-y mu:divide-line mu:overflow-y-auto mu:p-0"
      role="list" aria-label="Saved semesters" tabIndex={0}>
      {semesters.map((semester, index) => (
        <li key={semester.id} className="mu:py-3 mu:pr-2 mu:wrap-anywhere mu:first:pt-1">
          <div className="mu:flex mu:flex-wrap mu:items-baseline mu:justify-between mu:gap-x-3 mu:gap-y-0.5">
            <h3 className="mu:m-0 mu:text-sm mu:font-semibold">Semester {index + 1}</h3>
            <p className="mu:m-0 mu:text-xs mu:text-muted">
              {semester.term_id == null ? "Catalog only" : semester.term_name || `Term ${semester.term_id}`}
            </p>
          </div>
          {semester.courses.length > 0 ? (
            <ul role="list" className="mu:mt-2 mu:mb-0 mu:list-none mu:space-y-1 mu:p-0">
              {semester.courses.map((course) => (
                <li key={course.course_id} className="mu:flex mu:flex-wrap mu:items-baseline mu:gap-x-2 mu:rounded-sm mu:bg-canvas mu:px-2 mu:py-1 mu:text-xs">
                  <span className="mu:font-semibold">{course.course_code}</span>
                  {course.selected_section?.section_number != null && (
                    <span className="mu:text-muted">Section {course.selected_section.section_number}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : <p className="mu:m-0 mu:mt-1 mu:text-xs mu:text-muted">No courses in this plan.</p>}
        </li>
      ))}
    </ol>
  );
}

function DashboardPanel({ title, variant, to, action, count, children }) {
  const headingId = useId();
  return (
    <section className={`mu:student-focus mu:flex mu:min-w-0 mu:flex-col mu:rounded-panel mu:border mu:border-line mu:bg-paper mu:shadow-panel ${variant === "progress" ? "mu:md:col-span-2 mu:xl:col-span-1" : ""}`}
      aria-labelledby={headingId} tabIndex={-1}>
      <header className="mu:flex mu:items-center mu:gap-2.5 mu:px-5 mu:pt-5 mu:pb-4">
        <span className={`dashboard-icon dashboard-icon--${variant} mu:grid mu:size-9 mu:shrink-0 mu:place-items-center mu:rounded-xl mu:bg-mint mu:text-herd`} aria-hidden="true" />
        <h2 id={headingId} className="mu:m-0 mu:text-lg mu:leading-tight mu:font-semibold mu:tracking-tight">{title}</h2>
        {count > 0 && (
          <span className="mu:ml-auto mu:rounded-md mu:bg-mint mu:px-1.5 mu:py-0.5 mu:text-xs mu:font-semibold mu:text-herd mu:tabular-nums">
            {count}<span className="mu:sr-only"> saved</span>
          </span>
        )}
      </header>
      <div className="mu:min-w-0 mu:flex-1 mu:px-5 mu:pb-4">{children}</div>
      <Link className="mu:student-focus mu:flex mu:min-h-12 mu:items-center mu:justify-between mu:gap-3 mu:rounded-b-panel mu:border-t mu:border-line mu:px-5 mu:text-sm mu:font-semibold mu:motion-safe:transition-colors mu:hover:bg-audit-tint"
        to={to}><span className="mu:text-herd">{action}</span><span className="mu:text-herd" aria-hidden="true">→</span></Link>
    </section>
  );
}

export default function Dashboard() {
  const { record, reloadAcademics, canSave, dirty, evaluation, evaluate, saveError } = usePlanning();
  const [planner, setPlanner] = useState({ loading: true, semesters: [], error: null });
  const [attempt, setAttempt] = useState(0);
  const conflict = evaluation.error?.status === 409 || saveError?.status === 409;
  const audit = !conflict && evaluation.fingerprint === JSON.stringify(record.input) ? evaluation.data : null;
  const needsCreditEvidence = Boolean(record.revision && record.input.rows.length && !audit);

  useEffect(() => {
    // The provider reuses the saved revision when the form has no pending edits.
    if (needsCreditEvidence && canSave && !dirty && !evaluation.loading && !evaluation.error && !saveError) {
      void evaluate();
    }
  }, [needsCreditEvidence, canSave, dirty, evaluation.loading, evaluation.error, saveError, evaluate]);

  useEffect(() => {
    const controller = new AbortController();
    loadPlanner(controller.signal).then(({ semesters }) => {
      if (!controller.signal.aborted) setPlanner({ loading: false, semesters, error: null });
    }).catch((error) => {
      if (!controller.signal.aborted) setPlanner({ loading: false, semesters: [], error: error.message });
    });
    return () => controller.abort();
  }, [attempt]);

  return (
    <main className="mu:flex-1 mu:bg-canvas mu:font-campus mu:text-ink">
      <div className="mu:mx-auto mu:max-w-[87rem] mu:px-4 mu:pt-6 mu:pb-10 mu:sm:px-6 mu:lg:px-10 mu:lg:pt-9">
        <header className="dashboard-hero mu:relative mu:isolate mu:mb-5 mu:flex mu:min-h-40 mu:items-end mu:overflow-hidden mu:rounded-banner mu:bg-forest mu:px-6 mu:py-7 mu:text-paper mu:sm:px-8 mu:lg:mb-6 mu:lg:min-h-52 mu:lg:px-10 mu:lg:py-9">
          <div>
            <h1 className="mu:m-0 mu:font-display mu:text-page mu:font-normal mu:tracking-tight">Dashboard</h1>
            <p className="mu:m-0 mu:mt-2 mu:text-sm mu:leading-relaxed mu:text-leaf mu:sm:text-base">
              Review your coursework and plan your next semester.
            </p>
          </div>
        </header>

        <div className="mu:grid mu:grid-cols-1 mu:gap-4 mu:md:grid-cols-2 mu:xl:grid-cols-[1.1fr_1fr_1fr]">
          <DashboardPanel title="Degree Progress" variant="progress" to="/degree-audit" action="View degree audit">
            <DegreeProgress record={record} evaluation={evaluation} audit={audit} requestError={evaluation.error || saveError} />
          </DashboardPanel>

          <DashboardPanel title="Completed Coursework" variant="coursework" to="/academic-info" action="Manage coursework"
            count={record.loading || record.error ? 0 : record.input.rows.length}>
            {record.loading ? <DashboardLoading>Loading saved coursework…</DashboardLoading> : record.error ? (
              <DashboardLoadError message={record.error.message} action="Retry loading coursework" onRetry={reloadAcademics} />
            ) : <CompletedCoursework rows={record.input.rows} />}
          </DashboardPanel>

          <DashboardPanel title="Saved Semester Plans" variant="plans" to="/planner" action="Open planner" count={planner.semesters.length}>
            {planner.loading ? <DashboardLoading>Loading saved semesters…</DashboardLoading> : planner.error ? (
              <DashboardLoadError message={planner.error} action="Retry loading semesters" onRetry={() => {
                setPlanner({ loading: true, semesters: [], error: null });
                setAttempt((value) => value + 1);
              }} />
            ) : <SavedSemesters semesters={planner.semesters} />}
          </DashboardPanel>
        </div>

        <section className="mu:mt-5 mu:flex mu:flex-wrap mu:items-center mu:justify-between mu:gap-3 mu:rounded-panel mu:border mu:border-line mu:bg-paper mu:px-5 mu:py-3" aria-labelledby="dashboard-quick-heading">
          <h2 id="dashboard-quick-heading" className="mu:m-0 mu:text-base mu:font-semibold">Quick Access</h2>
          <div className="mu:flex mu:flex-wrap mu:gap-2">
            <QuickLink to="/courses">Explore courses</QuickLink>
            <QuickLink to="/advisor">AI Advisor</QuickLink>
          </div>
        </section>
      </div>
    </main>
  );
}

function QuickLink({ to, children }) {
  return (
    <Link className="mu:student-focus mu:inline-flex mu:min-h-11 mu:items-center mu:gap-3 mu:rounded-lg mu:border mu:border-line mu:px-3 mu:text-sm mu:font-semibold mu:motion-safe:transition-colors mu:hover:bg-mint"
      to={to}><span className="mu:text-herd">{children}</span><span className="mu:text-herd" aria-hidden="true">→</span></Link>
  );
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import CompletedCoursework from "../components/CompletedCoursework.jsx";
import { usePlanning } from "../context/planningContext.js";
import { loadPlanner } from "../lib/studentPlannerApi.js";
import "../styles/Dashboard.css";

const creditFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const percentFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, style: "percent" });

function DegreeProgress({ record, evaluation, audit, requestError }) {
  if (record.loading) return <p role="status" className="dashboard-note">Loading academic information…</p>;
  if (record.error) return <p className="dashboard-note">Degree progress is unavailable while coursework can’t be loaded.</p>;
  if (!record.revision) return <p className="dashboard-note">Save a program in Academic Info to start your degree audit.</p>;

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
      <div className="dashboard-program">
        <h3>{program.program_title ?? program.program_key}</h3>
        <span>Catalog {program.catalog_year}</span>
      </div>
      {hasCredits ? (
        <div className="dashboard-credit-progress">
          <div className="dashboard-credit-numbers">
            <div><strong>{creditFormat.format(credits)}</strong><span> / {creditFormat.format(target)} credits</span></div>
            <span className="dashboard-credit-percent">{percentFormat.format(filled / target)}</span>
          </div>
          <progress aria-label="Estimated credits toward the degree" max={target} value={filled}
            aria-valuetext={`${creditFormat.format(credits)} of ${creditFormat.format(target)} credits`} />
          <p className="dashboard-note">
            Estimated from saved coursework.
            {unknownCount > 0 && ` Credits unknown for ${unknownCount} ${unknownCount === 1 ? "course" : "courses"}.`}
          </p>
        </div>
      ) : (
        <p role={evaluation.loading ? "status" : undefined} className="dashboard-note">
          {!hasTarget ? "The credit target is unavailable for this program."
            : evaluation.loading ? "Checking saved course credits…"
            : requestError || audit ? "Credit totals are unavailable. Open Degree Audit to check your coursework."
            : "Open Degree Audit to check credit progress for your saved coursework."}
        </p>
      )}
    </>
  );
}

function SavedSemesters({ semesters }) {
  if (!semesters.length) return <p className="dashboard-note">No semester plans saved yet.</p>;

  return (
    <ol className="dashboard-semesters dashboard-scroll" aria-label="Saved semesters" tabIndex={0}>
      {semesters.map((semester, index) => (
        <li key={semester.id}>
          <h3>Semester {index + 1}</h3>
          <p className="dashboard-note">
            {semester.term_id == null ? "Catalog only" : semester.term_name || `Term ${semester.term_id}`}
          </p>
          {semester.courses.length > 0 ? (
            <ul className="dashboard-planned-courses">
              {semester.courses.map((course) => (
                <li key={course.course_id}>
                  {course.course_code}
                  {course.selected_section?.section_number != null && ` · Section ${course.selected_section.section_number}`}
                </li>
              ))}
            </ul>
          ) : <p className="dashboard-note">No courses in this plan.</p>}
        </li>
      ))}
    </ol>
  );
}

function DashboardPanel({ title, variant, to, action, count, children }) {
  return (
    <section className={`dashboard-panel dashboard-panel--${variant}`} aria-label={title}>
      <header className="dashboard-panel-heading">
        <span className="dashboard-panel-icon" aria-hidden="true" />
        <h2>{title}</h2>
        {count > 0 && <span className="dashboard-panel-count">{count}<span className="sr-only"> saved</span></span>}
      </header>
      <div className="dashboard-panel-body">{children}</div>
      <Link className="dashboard-panel-link" to={to}>{action}<span aria-hidden="true">→</span></Link>
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
    <main className="dashboard">
      <header className="dashboard-banner">
        <h1>Dashboard</h1>
        <p>Track your degree progress and plan your next semester.</p>
      </header>

      <div className="dashboard-overview">
        <DashboardPanel title="Degree Progress" variant="progress" to="/degree-audit" action="View degree audit">
          <DegreeProgress record={record} evaluation={evaluation} audit={audit} requestError={evaluation.error || saveError} />
        </DashboardPanel>

        <DashboardPanel title="Completed Coursework" variant="coursework" to="/academic-info" action="Manage coursework"
          count={record.loading || record.error ? 0 : record.input.rows.length}>
          {record.loading ? <p role="status" className="dashboard-note">Loading saved coursework…</p> : record.error ? (
            <div role="alert" className="dashboard-load-error">
              <p>{record.error.message}</p>
              <button type="button" onClick={reloadAcademics}>Retry loading coursework</button>
            </div>
          ) : <CompletedCoursework rows={record.input.rows} />}
        </DashboardPanel>

        <DashboardPanel title="Saved Semester Plans" variant="plans" to="/planner" action="Open planner" count={planner.semesters.length}>
          {planner.loading ? <p role="status" className="dashboard-note">Loading saved semesters…</p> : planner.error ? (
            <div role="alert" className="dashboard-load-error">
              <p>{planner.error}</p>
              <button type="button" onClick={() => {
                setPlanner({ loading: true, semesters: [], error: null });
                setAttempt((value) => value + 1);
              }}>Retry loading semesters</button>
            </div>
          ) : <SavedSemesters semesters={planner.semesters} />}
        </DashboardPanel>
      </div>

      <section className="dashboard-quick" aria-label="Quick Access">
        <h2>Quick Access</h2>
        <div>
          <Link to="/courses">Explore courses <span aria-hidden="true">→</span></Link>
          <Link to="/advisor">AI Advisor <span aria-hidden="true">→</span></Link>
        </div>
      </section>
    </main>
  );
}

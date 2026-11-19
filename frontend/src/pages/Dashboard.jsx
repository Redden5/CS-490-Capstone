import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import DashboardCard from "../components/DashboardCard.jsx";
import CompletedCoursework from "../components/CompletedCoursework.jsx";
import { usePlanning } from "../context/planningContext.js";
import { loadPlanner } from "../lib/studentPlannerApi.js";

function SavedSemesters({ semesters }) {
  if (!semesters.length) return <p className="dashboard-note">No semester plans saved yet.</p>;

  return (
    <ol className="dashboard-records">
      {semesters.map((semester, index) => (
        <li key={semester.id}>
          <strong>Semester {index + 1}</strong>
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

function Dashboard() {
  const { record, reloadAcademics, evaluation, stale } = usePlanning();
  const [planner, setPlanner] = useState({ loading: true, semesters: [], error: null });
  const [attempt, setAttempt] = useState(0);
  const program = record.revision ? record.input.program : null;
  const summary = record.revision && !stale ? evaluation.data?.summary : null;

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
      <h1>Dashboard</h1>

      <div className="dashboard-overview">
        <DashboardCard title="Degree Progress">
          {record.loading ? <p role="status">Loading academic information…</p> : record.error ? (
            <p className="dashboard-note">Degree progress is unavailable while coursework can’t be loaded.</p>
          ) : program ? (
            <>
              <p className="dashboard-program"><strong>{program.program_title ?? program.program_key}</strong></p>
              <p className="dashboard-note">Catalog {program.catalog_year}</p>
              {summary ? (
                <dl className="dashboard-audit">
                  <div><dt>Requirement entries matched by reported coursework</dt><dd>{summary.candidate_met_rule_occurrences}</dd></div>
                  <div><dt>Requirement entries with coursework remaining</dt><dd>{summary.candidate_not_met_rule_occurrences}</dd></div>
                </dl>
              ) : <p className="dashboard-note">Open Degree Audit for your latest requirement progress.</p>}
            </>
          ) : <p className="dashboard-note">Save a program in Academic Info to start your degree audit.</p>}
        </DashboardCard>

        <DashboardCard title="Completed Coursework">
          {record.loading ? <p role="status">Loading saved coursework…</p> : record.error ? (
            <div role="alert">
              <p>{record.error.message}</p>
              <button type="button" onClick={reloadAcademics}>Retry loading coursework</button>
            </div>
          ) : <CompletedCoursework rows={record.input.rows} />}
        </DashboardCard>

        <DashboardCard title="Saved Semester Plans">
          {planner.loading ? <p role="status">Loading saved semesters…</p> : planner.error ? (
            <div role="alert">
              <p>{planner.error}</p>
              <button type="button" onClick={() => {
                setPlanner({ loading: true, semesters: [], error: null });
                setAttempt((value) => value + 1);
              }}>Retry loading semesters</button>
            </div>
          ) : <SavedSemesters semesters={planner.semesters} />}
        </DashboardCard>
      </div>

      <DashboardCard title="Quick Access">
        <div className="quick-links">
          <Link className="primary-link" to="/degree-audit">Degree Audit</Link>
          <Link to="/planner">Open Planner</Link>
          <Link to="/courses">Explore Courses</Link>
          <Link to="/academic-info">Academic Info</Link>
        </div>
      </DashboardCard>
    </main>
  );
}

export default Dashboard;

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import CompletedCoursework from '../components/CompletedCoursework.jsx';
import PageHeading from '../components/PageHeading.jsx';
import { usePlanning } from '../context/planningContext.js';
import { loadPlanner } from '../lib/studentPlannerApi.js';
import { degreeProgress, requirementsMet, semesterPreview } from '../lib/dashboardPresentation.js';
import '../styles/Dashboard.css';

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, style: 'percent' });

function countLabel(count, noun, plural = `${noun}s`) {
  return `${number.format(count)} ${count === 1 ? noun : plural}`;
}

function Panel({ title, children, footer, to, count, variant }) {
  return <section className={`student-panel student-panel-${variant}`} aria-label={title}>
    <header><h2>{title}</h2>{count && <span className="panel-count">{count}</span>}</header><div className="student-panel-content">{children}</div>
    <Link className="student-panel-footer" to={to}>{footer}<span aria-hidden="true">→</span></Link>
  </section>;
}
function ShortcutIcon({ type }) {
  const paths = { courses: 'M12 6C9 4 6 4 3 5v15c3-1 6-1 9 1m0-15c3-2 6-2 9-1v15c-3-1-6-1-9 1V6Z', advisor: 'M4 4h16v12H9l-5 4V4Zm4 5h8m-8 3h5' };
  return <svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[type]} /></svg>;
}

export default function Dashboard() {
  const { record, reloadAcademics, canSave, dirty, evaluation, evaluate, saveError } = usePlanning();
  const [planner, setPlanner] = useState({ loading: true, semesters: [], preferences: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const conflict = evaluation.error?.status === 409 || saveError?.status === 409;
  const audit = !conflict && !record.loading && !record.error && evaluation.fingerprint === JSON.stringify(record.input) ? evaluation.data : null;
  const needsCreditEvidence = Boolean(record.revision && record.input.rows.length && !audit);
  useEffect(() => {
    if (needsCreditEvidence && canSave && !dirty && !evaluation.loading && !evaluation.error && !saveError) void evaluate();
  }, [needsCreditEvidence, canSave, dirty, evaluation.loading, evaluation.error, saveError, evaluate]);
  useEffect(() => {
    const controller = new AbortController();
    loadPlanner(controller.signal).then(({ semesters, preferences }) => {
      if (!controller.signal.aborted) setPlanner({ loading: false, semesters, preferences, error: null });
    }).catch((error) => {
      if (!controller.signal.aborted) setPlanner({ loading: false, semesters: [], preferences: null, error: error.message });
    });
    return () => controller.abort();
  }, [attempt]);

  const program = record.loading || record.error ? null : record.input.program;
  const progress = conflict ? null : degreeProgress(record, audit);
  const met = requirementsMet(audit);
  const courseworkAvailable = !record.loading && !record.error;
  const plansAvailable = !planner.loading && !planner.error;
  const preview = plansAvailable ? semesterPreview(planner.semesters, planner.preferences?.term_id) : null;
  const progressMessage = record.loading ? 'Loading academic information…' : record.error ? 'Coursework could not be loaded.' : !record.revision
    ? 'Degree progress is unavailable. Save your academic information to get started.'
    : evaluation.loading ? 'Checking your saved coursework…' : 'Degree progress is unavailable. Review your academic information.';
  function retryPlanner() {
    setPlanner((current) => ({ ...current, loading: true, error: null })); setAttempt((value) => value + 1);
  }

  return <main className="dashboard app-page student-dashboard">
    <PageHeading title="Dashboard" description="Your academic progress and semester plans, all in one place.">
      {program && <p className="student-program-label"><strong>{program.program_title || program.program_key}</strong><span>Catalog {program.catalog_year.replace('-', '–')}</span></p>}
    </PageHeading>
    {dirty && <p className="student-notice" role="status">You have unsaved coursework changes. This overview reflects your saved academic record.</p>}
    {conflict && <p className="student-notice" role="alert">Your academic record changed elsewhere. <button className="planning-link" type="button" onClick={reloadAcademics}>Reload saved coursework</button></p>}
    <section className="degree-feature" aria-labelledby="degree-heading">
      <div className="degree-dial">
        <svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">
          <circle cx="50" cy="50" r="46" fill="#123f32" stroke="#648372" strokeWidth="6" />
          {progress?.fraction > 0 && <circle cx="50" cy="50" r="46" fill="none" stroke="#d7e99b" strokeWidth="6"
            pathLength="1" strokeDasharray={`${progress.fraction} 1`} transform="rotate(-90 50 50)" />}
        </svg>
        <div><strong>{progress ? percent.format(progress.fraction) : '—'}</strong><span>estimated complete</span></div>
      </div>
      <div className="degree-story">
        <h2 id="degree-heading">Degree progress</h2>
        {progress ? <><p className="degree-credits"><strong>{number.format(progress.credits)}</strong> of {number.format(progress.target)} credits reported</p>
          <p>Unofficial estimate from saved coursework.{progress.unknownCount > 0 && ` Credits are unknown for ${progress.unknownCount} saved course entries.`}</p></> : <p role="status">{progressMessage}</p>}
        <p>{met == null ? 'Requirements met —' : `${countLabel(met, 'requirement')} met`}</p>
        {record.error && <div role="alert"><p className="student-support">{record.error.message}</p><button className="planning-button secondary" type="button" onClick={reloadAcademics}>Retry academic information</button></div>}
        {(evaluation.error || saveError) && !conflict && <div role="alert"><p className="student-support">The latest audit could not be completed. {audit ? 'The previous saved-record estimate is shown.' : 'Your saved coursework is still available.'}</p><Link className="planning-link" to="/degree-audit">Review audit error or retry →</Link></div>}
        {!record.loading && !record.error && !record.revision && <Link className="planning-button secondary" to="/academic-info">Add academic information</Link>}
        <Link className="degree-audit-link" to="/degree-audit">View degree audit →</Link>
      </div>
    </section>
    <div className="student-previews">
      <Panel variant="semester" count={plansAvailable ? countLabel(planner.semesters.length, 'saved plan') : null} title="Saved Semester Plans" to="/planner" footer={plansAvailable && planner.semesters.length > 1 ? 'View All Saved Plans' : 'Open Planner'}>
        {planner.loading ? <p role="status" className="student-support">Loading saved semester plans…</p> : planner.error ? <div role="alert"><p className="student-support">{planner.error}</p><button className="planning-button secondary" type="button" onClick={retryPlanner}>Retry loading plans</button></div>
          : !preview ? <><p className="student-support">You haven’t saved a semester plan yet.</p></> : <>
            <p className="student-plan-term">{preview.plan.term_name || (preview.plan.term_id == null ? 'Catalog plan · no term selected' : `Academic term ${preview.plan.term_id}`)}</p>
            <p className="student-plan-name">{preview.label}</p>
            <p className="student-plan-totals">{preview.plan.courses.length} {preview.plan.courses.length === 1 ? 'course' : 'courses'} <span aria-hidden="true">·</span> {preview.minimum == null ? 'Credits unavailable' : preview.minimum === preview.maximum ? `${number.format(preview.minimum)} planned credits` : `${number.format(preview.minimum)}–${number.format(preview.maximum)} planned credits`}</p>
            {preview.courses.length ? <ul className="student-plan-courses">{preview.courses.map((course) => <li key={course.course_id}>{course.course_code}</li>)}</ul> : <p className="student-support">No courses added to this plan.</p>}
            {preview.additionalCount > 0 && <p className="student-support">+{preview.additionalCount} more {preview.additionalCount === 1 ? 'course' : 'courses'}</p>}

          </>}
      </Panel>
      <Panel variant="coursework" count={courseworkAvailable ? countLabel(record.input.rows.length, 'course entry', 'course entries') : null} title="Completed Coursework" to="/academic-info" footer={courseworkAvailable && !record.input.rows.length ? 'Add coursework' : 'View all coursework'}>
        {record.loading ? <p role="status" className="student-support">Loading saved coursework…</p> : record.error ? <div role="alert"><p className="student-support">Coursework could not be loaded.</p><button className="planning-button secondary" type="button" onClick={reloadAcademics}>Retry loading coursework</button></div>
          : <CompletedCoursework rows={record.input.rows} />}
      </Panel>
    </div>
    <nav className="dashboard-resources" aria-label="Academic resources">
      <Link to="/courses"><ShortcutIcon type="courses" />Explore courses <span aria-hidden="true">→</span></Link>
      <Link to="/advisor"><ShortcutIcon type="advisor" />AI Advisor (preview) <span aria-hidden="true">→</span></Link>
    </nav>
  </main>;
}

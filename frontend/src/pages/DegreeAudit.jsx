import { matchesCourseSearch } from '../lib/courseSearch.js';
import PageHeading from '../components/PageHeading.jsx';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { CourseEvidence, RequirementEvidence } from '../components/PlanningEvidence.jsx';
import { explain, requirementGroups } from '../lib/planningPresentation.js';
import '../styles/Planning.css';
import '../styles/DegreeAudit.css';

function PrerequisiteResults({ courses }) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [limit, setLimit] = useState(25);
  const matching = courses.filter((course) => (!status || course.status === status) &&
    matchesCourseSearch(course, search));
  return <section className="planning-card" aria-labelledby="prerequisite-evidence-title">
    <h2 id="prerequisite-evidence-title">Prerequisite evidence</h2>
    <p>These results use your reported coursework. Met conditions do not confirm enrollment eligibility. Unknown results need additional evidence or review.</p>
    <div className="planning-filter-row">
      <label className="planning-field">Find a course<input value={search} placeholder="Course code" onChange={(e) => { setSearch(e.target.value); setLimit(25); }} /></label>
      <label className="planning-field">Evidence status<select value={status} onChange={(e) => { setStatus(e.target.value); setLimit(25); }}>
        <option value="">All results</option><option value="MET">Met by reported evidence</option><option value="UNMET">Condition not met</option><option value="UNKNOWN">Uncertain</option>
      </select></label>
    </div>
    <p>{matching.length} matching {matching.length === 1 ? 'course' : 'courses'}</p>
    {!matching.length && <p className="planning-empty">No results for this selection. An absent course has not been evaluated.</p>}
    {matching.slice(0, limit).map((course) => <CourseEvidence key={course.course_code} course={course} />)}
    {matching.length > limit && <button className="planning-button secondary" type="button" onClick={() => setLimit(limit + 25)}>Show more courses</button>}
  </section>;
}

export default function DegreeAudit() {
  const { programs, record, reloadAcademics, canSave, saving, saveError, dirty, evaluation, evaluate, stale } = usePlanning();
  const result = evaluation.data;
  const [remainingOnly, setRemainingOnly] = useState(false);
  useEffect(() => {
    if (record.revision && canSave && !dirty && !result && !evaluation.loading && !evaluation.error && !saveError) {
      void evaluate();
    }
  }, [record.revision, canSave, dirty, result, evaluation.loading, evaluation.error, saveError, evaluate]);
  const eligibility = Object.fromEntries((result?.course_eligibility.courses ?? []).map((course) => [course.course_code, course]));
  const flexible = Object.fromEntries((result?.flexible_candidates.requirements ?? []).map((requirement) => [requirement.requirement_id, requirement]));
  const complete = result?.summary.candidate_met_rule_occurrences ?? 0;
  const remaining = result?.summary.candidate_not_met_rule_occurrences ?? 0;
  const review = result ? result.requirements.length - complete - remaining : 0;
  const credits = result?.distinct_course_credit_evidence;
  const reportedCount = result?.completed_course_codes_entered?.length ?? 0;

  const groups = requirementGroups((result?.requirements ?? []).filter((rule) =>
    !remainingOnly || rule.planning_disposition === 'CANDIDATE_COURSEWORK_REMAINING'));
  const breakdown = [
    { label: 'Provisionally satisfied', value: complete, tone: 'met' },
    { label: 'Coursework remaining', value: remaining, tone: 'remaining' },
    { label: 'Needs review', value: review, tone: 'review' },
  ];

  return <main className="planning-page app-page audit-page">
    <PageHeading title="Degree Audit" description="Review your reported progress, remaining coursework, and course options.">
      <button className="planning-button" type="button" disabled={evaluation.loading || !canSave || saving} onClick={evaluate}>
        {result ? 'Re-evaluate coursework' : 'Evaluate coursework'}
      </button>
      <Link className="planning-button secondary" to="/academic-info">Edit Academic Info →</Link>
    </PageHeading>
    <p className="audit-disclaimer"><strong>Unofficial planning audit.</strong> It does not verify your transcript, graduation requirements, or permission to enroll.</p>
    {evaluation.loading && <p role="status" className="planning-notice">Evaluating coursework and catalog evidence…</p>}
    {stale && <p className="planning-notice warning" role="status">Outdated results: your academic information has changed. The report below reflects your last successful evaluation.</p>}
    {evaluation.error && <div className="planning-notice warning" role="alert"><p>{evaluation.error.message}</p>
      {result && <p>Your last successful report is still shown below.</p>}
      {[404, 409].includes(evaluation.error.status) && <button type="button" className="planning-link" onClick={reloadAcademics}>Reload saved coursework</button>}
    </div>}
    {record.loading && <p role="status">Loading saved coursework…</p>}
    {record.error && <p role="alert">{record.error.message}{' '}
      <button className="planning-link" type="button" onClick={reloadAcademics}>Retry loading coursework</button></p>}
    {saveError && <p role="alert">{saveError.message}{' '}
      <Link to="/academic-info">Open Academic Info</Link></p>}
    {programs.error && <p role="alert">{programs.error} <button className="planning-link" type="button" onClick={reloadAcademics}>Retry loading programs</button></p>}
    {!result ? <section className="planning-card planning-empty">
      <h2>Your audit starts with Academic Info</h2>
      <p>Select your program and enter successfully completed courses. You can evaluate an empty list if you have not completed any courses yet.</p>
      <Link className="planning-link" to="/academic-info">Enter coursework →</Link>
      <Link className="audit-empty-planner" to="/planner">Open Planner →</Link>
    </section> : <>
      <section className="audit-overview" aria-labelledby="audit-program-title">
        <div>
          <p className="audit-report-state">{stale ? 'Outdated report' : 'Last successful evaluation'}</p>
          <h2 id="audit-program-title">{result.degree.program_title}</h2>
          <p>Catalog {result.degree.catalog_year} · {reportedCount} distinct reported {reportedCount === 1 ? 'course' : 'courses'}</p>
          <Link className="audit-planner" to="/planner">Open Planner →</Link>
        </div>
        <div className="audit-breakdown">
          <h3>Requirement breakdown</h3>
          <div className="audit-track" aria-hidden="true">{breakdown.filter(item => item.value > 0).map(item =>
            <span key={item.tone} className={item.tone} style={{ flexGrow: item.value }} />)}</div>
          <dl>{breakdown.map(item => <div key={item.tone}><dt><i aria-hidden="true" className={item.tone} />{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
        </div>
      </section>
      <div className="audit-report-grid">
      <section className="planning-card" aria-labelledby="degree-requirements-title">
        <h2 id="degree-requirements-title">Degree requirements</h2>
        <p>Expand a requirement to review its options, bundles, and evidence. Credit estimates across requirements must not be added together.</p>
        <label className="audit-toggle"><input type="checkbox" checked={remainingOnly} onChange={event => setRemainingOnly(event.target.checked)} />Show only coursework remaining</label>
        {groups.length === 0 && <p role="status">{remainingOnly ? 'No requirements marked as coursework remaining.' : 'No requirements returned for this audit.'}</p>}
        {groups.map(([name, requirements]) => <section className="planning-requirement-group" key={name}>
          <h3>{name}</h3>
          {requirements.map((requirement) => <RequirementEvidence key={requirement.rule_id} requirement={requirement}
            flexible={flexible[requirement.rule_id]} eligibility={eligibility} />)}
        </section>)}
      </section>
      <section className="planning-card audit-evidence-note" aria-labelledby="audit-evidence-title">
        <h2 id="audit-evidence-title">Audit evidence</h2>
        <p>Prerequisites: <strong>{explain(result.course_eligibility.evaluation_status)}</strong> · Flexible course options: <strong>{explain(result.flexible_candidates.generation_status)}</strong></p>
        {!result.candidate_option_coverage?.unmet_attribute_option_lists_enumerated && <p className="planning-notice warning">Some requirement categories do not have a complete list of course options. Missing options do not mean the requirements are satisfied.</p>}
        <p>Known credit subtotal for distinct reported courses: <strong>{credits?.known_credit_subtotal ?? 'Unavailable'}</strong>. This is a catalog/report estimate, not verified earned credit or a degree completion percentage.</p>
        {credits?.courses_without_known_credits?.length > 0 && <p>Credits still unknown for: {credits.courses_without_known_credits.join(', ')}.</p>}
        <details><summary>Warnings and retrieval details</summary>
          <ul>{(result.warnings ?? []).map((warning, i) => <li key={i}>{warning}</li>)}</ul>
          <p>Prerequisite retrieval: {explain(result.course_eligibility.retrieval_status)} · Source verification: {explain(result.course_eligibility.source_verification)}</p>
          <ul>{[...(result.course_eligibility.issues ?? []), ...(result.flexible_candidates.retrieval_issues ?? []),
            ...(result.flexible_candidates.completed_course_attribute_evidence?.issues ?? [])].map((issue, i) => <li key={i}>{explain(issue)}</li>)}</ul>
        </details>
      </section>
      </div>
      <PrerequisiteResults key={evaluation.fingerprint} courses={result.course_eligibility.courses} />
    </>}
  </main>;
}

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { CourseEvidence, RequirementEvidence } from '../components/PlanningEvidence.jsx';
import { explain, requirementGroups } from '../lib/planningPresentation.js';
import '../styles/Planning.css';

function PrerequisiteResults({ courses }) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [limit, setLimit] = useState(25);
  const matching = courses.filter((course) => (!status || course.status === status) &&
    course.course_code.replaceAll(' ', '').toLowerCase().includes(search.replaceAll(' ', '').toLowerCase()));
  return <section className="planning-card" aria-labelledby="prerequisite-evidence-title">
    <h2 id="prerequisite-evidence-title">Prerequisite evidence</h2>
    <p>These results use your reported coursework. Met conditions do not confirm enrollment eligibility. Unknown results need additional evidence or review.</p>
    <div className="planning-filter-row">
      <label className="planning-field">Find a course<input value={search} placeholder="Course code" onChange={(e) => { setSearch(e.target.value); setLimit(25); }} /></label>
      <label className="planning-field">Evidence status<select value={status} onChange={(e) => { setStatus(e.target.value); setLimit(25); }}>
        <option value="">All results</option><option value="MET">Met by reported evidence</option><option value="UNMET">Condition not met</option><option value="UNKNOWN">Uncertain</option>
      </select></label>
    </div>
    <p>{matching.length} matching courses</p>
    {!matching.length && <p className="planning-empty">No results for this selection. An absent course has not been evaluated.</p>}
    {matching.slice(0, limit).map((course) => <CourseEvidence key={course.course_code} course={course} />)}
    {matching.length > limit && <button className="planning-button secondary" type="button" onClick={() => setLimit(limit + 25)}>Show more courses</button>}
  </section>;
}

export default function DegreeAudit() {
  const { input, programs, refreshPrograms, evaluation, evaluate, stale } = usePlanning();
  const result = evaluation.data;
  const eligibility = Object.fromEntries((result?.course_eligibility.courses ?? []).map((course) => [course.course_code, course]));
  const flexible = Object.fromEntries((result?.flexible_candidates.requirements ?? []).map((requirement) => [requirement.requirement_id, requirement]));
  const complete = result?.summary.candidate_met_rule_occurrences ?? 0;
  const remaining = result?.summary.candidate_not_met_rule_occurrences ?? 0;
  const review = result ? result.requirements.length - complete - remaining : 0;
  const credits = result?.distinct_course_credit_evidence;
  const reportedCount = result?.completed_course_codes_entered?.length ?? 0;

  return <main className="planning-page">
    <header className="planning-heading"><div><p className="planning-eyebrow">Based on reported coursework</p><h1>Degree Audit</h1>
      <p>Explore requirement progress and course options for your program.</p></div>
      <Link className="planning-link" to="/academic-info">Edit Academic Info →</Link>
    </header>
    <p className="planning-notice">This is an unofficial planning audit. It does not verify your transcript, graduation requirements, or permission to enroll.</p>
    {evaluation.loading && <p role="status" className="planning-notice">Evaluating coursework and catalog evidence…</p>}
    {stale && <p className="planning-notice warning" role="status">Outdated results: your academic information has changed. The report below reflects your last successful evaluation.</p>}
    {evaluation.error && <div className="planning-notice warning" role="alert"><p>{evaluation.error.message}</p>
      {result && <p>Your last successful report is still shown below.</p>}
      {evaluation.error.status === 404 && <button type="button" className="planning-link" onClick={refreshPrograms}>Refresh programs</button>}
    </div>}
    {programs.error && <p role="alert">{programs.error} <button className="planning-link" type="button" onClick={refreshPrograms}>Retry loading programs</button></p>}
    <div className="planning-actions">
      <button className="planning-button" type="button" disabled={evaluation.loading || programs.loading || Boolean(programs.error) || !input.program} onClick={evaluate}>
        {result ? 'Re-evaluate coursework' : 'Evaluate coursework'}
      </button>
      <Link className="planning-button secondary" to="/planner">Open Planner</Link>
    </div>
    {!result ? <section className="planning-card planning-empty">
      <h2>Your audit starts with Academic Info</h2>
      <p>Select your program and enter successfully completed courses. You can evaluate an empty list if you have not completed any courses yet.</p>
      <Link className="planning-link" to="/academic-info">Enter coursework →</Link>
    </section> : <>
      <section className="planning-card planning-report-heading">
        <div><h2>{result.degree.program_title}</h2><p>Catalog {result.degree.catalog_year} · {reportedCount} distinct reported {reportedCount === 1 ? 'course' : 'courses'}</p></div>
        <span className="planning-badge review">{stale ? 'Outdated report' : 'Last successful evaluation'}</span>
      </section>
      <div className="planning-stats" aria-label="Requirement progress">
        <section className="planning-card"><strong>{complete}</strong><h2>Provisionally satisfied</h2><p>Requirements matched by reported evidence</p></section>
        <section className="planning-card"><strong>{remaining}</strong><h2>Coursework remaining</h2><p>Requirements with additional coursework indicated</p></section>
        <section className="planning-card"><strong>{review}</strong><h2>Needs review</h2><p>Unresolved rules or insufficient evidence</p></section>
      </div>
      <section className="planning-card" aria-labelledby="audit-evidence-title">
        <h2 id="audit-evidence-title">Evidence available for this audit</h2>
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
      <section className="planning-card" aria-labelledby="degree-requirements-title">
        <h2 id="degree-requirements-title">Degree requirements</h2>
        <p>Expand a requirement to review its options, bundles, and evidence. Credit estimates across requirements must not be added together.</p>
        {requirementGroups(result.requirements).map(([name, requirements]) => <section className="planning-requirement-group" key={name}>
          <h3>{name}</h3>
          {requirements.map((requirement) => <RequirementEvidence key={requirement.rule_id} requirement={requirement}
            flexible={flexible[requirement.rule_id]} eligibility={eligibility} />)}
        </section>)}
      </section>
      <PrerequisiteResults key={evaluation.fingerprint} courses={result.course_eligibility.courses} />
    </>}
  </main>;
}

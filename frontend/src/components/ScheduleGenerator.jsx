import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { meetingLabel, scheduleReason, scheduleRequest, selectedSectionIds } from '../lib/scheduleApi.js';

function Messages({ values }) {
  return values?.length > 0 && <ul>{[...new Set(values)].map((value) => <li key={value}>{scheduleReason(value)}</li>)}</ul>;
}

function SwapPicker({ code, option, plan, handoff, termId, onApply, onClose }) {
  const alternatives = [...new Set(plan.choice_groups.filter((g) => g.course_codes.includes(code))
    .flatMap((g) => g.course_codes))].filter((c) => c === code || (!option.sections[c] && !plan.required_courses.includes(c)));
  const canSwapCourse = !plan.required_courses.includes(code);
  const [course, setCourse] = useState(code);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState({ loading: true });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    scheduleRequest('sections', { course_code: course, term_id: termId }, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setResult({ loading: false, sections: data.sections }); })
      .catch((e) => { if (!controller.signal.aborted) setResult({ loading: false, error: e.message }); });
    return () => controller.abort();
  }, [course, termId, attempt]);
  useEffect(() => () => pending.current?.abort(), []);

  async function swap(sectionId) {
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    const sameCourse = course === code;
    try {
      const data = await scheduleRequest(sameCourse ? 'swap-section' : 'swap-course', {
        handoff, term_id: termId, current_sections: selectedSectionIds(option.sections),
        ...(sameCourse ? { course_code: code } : { old_course_code: code, new_course_code: course }),
        new_section_id: sectionId,
      }, controller.signal);
      if (controller.signal.aborted) return;
      if (!data.ok) { setError(data.errors.map(scheduleReason).join('; ')); return; }
      onApply({ ...option, ...data,
        unscheduled_required: plan.required_courses.filter((c) => !data.sections[c]),
      });
      onClose();
    } catch (e) {
      if (!controller.signal.aborted) setError(e.message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="planner-swap" aria-label={`Change ${code}`} aria-busy={busy}>
    <h4>Change {code}</h4>
    {canSwapCourse && alternatives.length > 1 && <label className="planner-term-label">Course alternative
      <select value={course} disabled={busy} onChange={(e) => {
        setCourse(e.target.value); setResult({ loading: true }); setError(null);
      }}>{alternatives.map((c) => <option key={c}>{c}</option>)}</select>
    </label>}
    {error && <p role="alert">{error}</p>}
    {result.loading ? <p role="status">Loading sections…</p> : result.error ? <p role="alert">{result.error} <button type="button" onClick={() => {
      setResult({ loading: true }); setAttempt((n) => n + 1);
    }}>Retry</button></p> : !result.sections.length ? <p>No sections are recorded for this course in this term.</p> : <ul className="planner-course-list">
      {result.sections.map((section) => <li key={section.section_id}>
        <p>Section {section.section_number || section.section_id} · CRN {section.crn || 'not listed'} · {section.section_status || 'Status unknown'} · Seats {section.enrolled ?? '?'}/{section.capacity ?? '?'}</p>
        <p>{section.meetings.length ? section.meetings.map(meetingLabel).join(', ') : 'Meeting times TBA'}</p>
        <button type="button" className="planner-secondary-button" disabled={busy || option.sections[course]?.section_id === section.section_id}
          onClick={() => swap(section.section_id)}>{option.sections[course]?.section_id === section.section_id ? 'Selected' : 'Use this section'}</button>
      </li>)}
    </ul>}
    <button type="button" className="planner-secondary-button" onClick={onClose}>Close</button>
  </section>;
}

function ScheduleOption({ option, index, plan, handoff, termId, onApply }) {
  const [editing, setEditing] = useState(null);
  return <article className="planner-semester-card planner-generated-option">
    <h3>Schedule {index + 1} · {option.total_credits} credits</h3>
    <p>{option.status === 'TENTATIVE' ? 'Tentative — evidence needs review' : 'Known checks passed — confirm before registration'}</p>
    <ul className="planner-course-list">
      {Object.entries(option.sections).map(([code, section]) => <li className="planner-course-slot" key={code}>
        <Link className="planner-course-link" to={`/courses/${section.course_id}`}>{code}</Link>
        <p>{plan.course_credit_hours[code]} credits · Section {section.section_number || section.section_id} · CRN {section.crn || 'not listed'}</p>
        <p>{section.meetings.length ? section.meetings.map(meetingLabel).join(', ') : 'Meeting times TBA'}</p>
        <button type="button" className="planner-secondary-button" onClick={() => setEditing(code)}>Change section{!plan.required_courses.includes(code) ? ' or course' : ''}</button>
      </li>)}
    </ul>
    {option.warnings.length > 0 && <details><summary>Review warnings ({option.warnings.length})</summary><Messages values={option.warnings} /></details>}
    {option.unscheduled_required.length > 0 && <details><summary>Required courses left for another semester ({option.unscheduled_required.length})</summary><p>{option.unscheduled_required.join(', ')}</p></details>}
    {editing && <SwapPicker key={editing} code={editing} option={option} plan={plan} handoff={handoff} termId={termId}
      onApply={onApply} onClose={() => setEditing(null)} />}
  </article>;
}

function GeneratorForm({ handoff, terms, loadingTerms, termError }) {
  const [termId, setTermId] = useState('');
  const [target, setTarget] = useState('15');
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(null);
  const selectedTerm = terms.find((t) => String(t.term_id) === termId);
  useEffect(() => () => pending.current?.abort(), []);

  function clear() {
    pending.current?.abort(); setBusy(false); setResult(null); setError(null);
  }
  async function generate(event) {
    event.preventDefault();
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await scheduleRequest('generate', {
        handoff, term_id: Number(termId), target_credits: Number(target), max_results: 5,
      }, controller.signal);
      if (!controller.signal.aborted) setResult(data);
    } catch (e) {
      if (!controller.signal.aborted) setError(e.message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return <>
    <form className="planner-generation-form" onSubmit={generate}>
      <label className="planner-term-label">Academic term
        <select required value={termId} disabled={loadingTerms || termError} onChange={(e) => { clear(); setTermId(e.target.value); }}>
          <option value="">Select an active term</option>
          {terms.map((term) => <option key={term.term_id} value={term.term_id}>{term.term_name}</option>)}
        </select>
      </label>
      <label className="planner-term-label">Target credits
        <input type="number" min="1" max="21" step="0.5" required value={target} onChange={(e) => { clear(); setTarget(e.target.value); }} />
      </label>
      <button type="submit" className="planner-primary-button" disabled={busy || !selectedTerm || loadingTerms || termError}>Generate Schedules</button>
      {busy && <><p role="status">Finding compatible sections…</p><button type="button" className="planner-secondary-button" onClick={clear}>Cancel</button></>}
    </form>
    {!loadingTerms && !termError && !terms.length && <p>No active terms are available. Ask your project administrator to load term offerings.</p>}
    {error && <p role="alert">{error}</p>}
    {result && <div aria-live="polite">
      <h3>Draft schedules for {selectedTerm?.term_name}</h3>
      <p>Based on your latest Degree Audit. These drafts do not save or register courses. Leaving this page clears them.</p>
      {result.search_truncated && <p role="status">The search reached its limit. These are partial results; a better schedule may exist.</p>}
      {!result.options.length && <p>No compatible schedule was found. Review the excluded courses below or choose another term.</p>}
      <Messages values={result.handoff_warnings} />
      {Object.keys(result.excluded_courses).length > 0 && <details><summary>Courses excluded from generation</summary>
        <ul>{Object.entries(result.excluded_courses).map(([code, reason]) => <li key={code}>{code}: {scheduleReason(reason)}</li>)}</ul>
      </details>}
      <div className="planner-generated-grid">{result.options.map((option, index) => <ScheduleOption key={index}
        option={option} index={index} plan={result} handoff={handoff} termId={Number(termId)} onApply={(updated) => setResult((current) => ({
          ...current, options: current.options.map((item, i) => i === index ? updated : item),
        }))} />)}</div>
    </div>}
  </>;
}

export default function ScheduleGenerator({ terms, loadingTerms, termError }) {
  const { evaluation, stale } = usePlanning();
  return <section className="dashboard-card planner-generator" aria-labelledby="schedule-generator-title">
    <h2 id="schedule-generator-title">Generate a semester schedule</h2>
    <p>Find compatible sections for remaining required courses and supported course choices. Flexible Core II options and course/lab bundles still need manual planning.</p>
    <p>This is unofficial planning. Unknown prerequisites or meeting times produce tentative schedules.</p>
    {!evaluation.data || stale || evaluation.loading ? <p role="status">
      {stale ? 'Your coursework changed. Update your Degree Audit before generating schedules.' : evaluation.loading ? 'Wait for your coursework evaluation to finish.' : 'Evaluate your coursework before generating schedules.'}
      {' '}<Link to="/academic-info">Open Academic Info</Link>
    </p> : <GeneratorForm key={evaluation.revision} handoff={evaluation.data} terms={terms}
      loadingTerms={loadingTerms} termError={termError} />}
  </section>;
}

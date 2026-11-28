import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { advisorRecommendations, draftCredits } from '../lib/advisorRecommendations.js';
import { loadPlanner, saveSemester } from '../lib/studentPlannerApi.js';
import { supabase } from '../lib/supabaseClient.js';

export default function AdvisorPersonalPlan({ onRequirements }) {
  const { record, dirty, stale, evaluation, evaluate, canSave, saving, saveError } = usePlanning();
  const [planner, setPlanner] = useState({ loading: true, semesters: [], error: '' });
  const [attempt, setAttempt] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [catalog, setCatalog] = useState({ key: '', courses: [], error: '' });
  const [operation, setOperation] = useState('');
  const [message, setMessage] = useState('');
  const [newDraft, setNewDraft] = useState(() => ({
    id: crypto.randomUUID(), courses: [], term_id: null, term_name: null, generated_summary: null,
  }));
  const pending = useRef(false);
  const selected = planner.semesters.find((semester) => semester.id === selectedId);
  const draft = selected ?? newDraft;
  const handoff = !dirty && !stale && !record.loading && !record.error ? evaluation.data : null;
  const recommendations = advisorRecommendations(handoff, draft.courses);
  const codes = recommendations.map((course) => course.code).join(',');
  const catalogKey = `${draft.term_id ?? 'catalog'}/${codes}/${attempt}`;
  const catalogReady = catalog.key === catalogKey;
  const catalogByCode = new Map((catalogReady ? catalog.courses : []).map((course) => [course.course_code, course]));
  const suggestions = recommendations.filter((course) => catalogByCode.has(course.code)).slice(0, 6);

  useEffect(() => {
    if (record.revision && canSave && !dirty && !evaluation.data && !evaluation.loading && !evaluation.error && !saveError) {
      void evaluate();
    }
  }, [record.revision, canSave, dirty, evaluation, saveError, evaluate]);

  useEffect(() => {
    const controller = new AbortController();
    loadPlanner(controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setPlanner({ loading: false, semesters: data.semesters, error: '' });
      setSelectedId((current) => data.semesters.some((semester) => semester.id === current)
        ? current : data.semesters[0]?.id ?? '');
    }).catch((error) => {
      if (!controller.signal.aborted) setPlanner({ loading: false, semesters: [], error: error.message });
    });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        let courses = [];
        if (codes) {
          const columns = 'course_id,course_code,course_title,min_credit_hours,max_credit_hours';
          const courseCodes = codes.split(',');
          for (let start = 0; start < courseCodes.length; start += 80) {
            let query = supabase.from('course').select(draft.term_id ? `${columns},coursesection!inner(term_id)` : columns)
              .in('course_code', courseCodes.slice(start, start + 80));
            if (draft.term_id) query = query.eq('coursesection.term_id', draft.term_id);
            const { data, error } = await query.abortSignal(controller.signal);
            if (error) throw error;
            if (controller.signal.aborted) return;
            courses.push(...(data ?? []));
          }
        }
        if (!controller.signal.aborted) setCatalog({ key: catalogKey, courses, error: '' });
      } catch {
        if (!controller.signal.aborted) setCatalog({ key: catalogKey, courses: [], error: 'Unable to load course details and offerings.' });
      }
    }
    void load();
    return () => controller.abort();
  }, [codes, draft.term_id, catalogKey]);

  async function addCourse(suggestion) {
    const course = catalogByCode.get(suggestion.code);
    if (pending.current || !course || !handoff || planner.loading || planner.error) return;
    pending.current = true;
    setOperation(suggestion.code);
    setMessage('');
    try {
      const saved = await saveSemester({
        ...draft,
        courses: [...draft.courses, {
          course_id: course.course_id, course_code: course.course_code, course_title: course.course_title ?? '',
          min_credit_hours: Number(course.min_credit_hours), max_credit_hours: Number(course.max_credit_hours),
          selected_section: null,
        }],
      });
      setPlanner((current) => ({ ...current, semesters: current.semesters.some((semester) => semester.id === saved.id)
        ? current.semesters.map((semester) => semester.id === saved.id ? saved : semester)
        : [...current.semesters, saved] }));
      setSelectedId(saved.id);
      if (saved.id === newDraft.id) setNewDraft((current) => ({ ...current, id: crypto.randomUUID() }));
      setMessage(`${course.course_code} saved to your draft.`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      pending.current = false;
      setOperation('');
    }
  }

  const unavailable = record.loading ? 'Loading your coursework…'
    : record.error ? record.error.message
    : dirty || stale ? 'Update your Degree Audit to refresh course suggestions.'
    : evaluation.loading ? 'Checking your remaining degree requirements…'
    : evaluation.error ? evaluation.error.message
    : saveError ? saveError.message
    : !handoff ? 'Add and evaluate your coursework to get course suggestions.' : '';

  return <div className="advisor-workspace">
    <section className="advisor-panel advisor-recommendations" aria-labelledby="advisor-courses-title">
      <h2 id="advisor-courses-title" tabIndex={-1}>Courses to consider</h2>
      <p className="advisor-panel-intro">Based on your latest Degree Audit{draft.term_name ? ` and recorded offerings for ${draft.term_name}` : ''}.</p>
      {unavailable ? <div className="advisor-state">
        <p role="status">{unavailable}</p>
        <div className="advisor-state-actions">
          <Link className="advisor-text-button" to="/academic-info">Open Academic Info</Link>
          {canSave && !dirty && !evaluation.loading && <button className="advisor-outline-button" type="button"
            disabled={saving} onClick={evaluate}>Update Degree Audit</button>}
        </div>
      </div> : !catalogReady ? <p className="advisor-state" role="status">Loading course options…</p>
        : catalog.error ? <p className="advisor-state" role="alert">{catalog.error}</p>
        : <div className="advisor-course-list">
          {!suggestions.length && <p className="advisor-state">No course suggestions match this draft and the current audit.</p>}
          {suggestions.map((suggestion) => {
            const course = catalogByCode.get(suggestion.code);
            return <article className="advisor-course-card" key={suggestion.code}>
              <div className="advisor-course-copy">
                <h3>{suggestion.code} · {course.course_title}</h3>
                <p>{suggestion.reason}</p>
                <p className="advisor-course-source" data-status={suggestion.status}>{suggestion.status === 'MET'
                  ? 'Prerequisites met by reported evidence.' : 'Prerequisite evidence needs review.'}</p>
              </div>
              <div className="advisor-course-actions">
                <button type="button" className="advisor-text-button" onClick={() => onRequirements(suggestion.code)}>View requirements</button>
                <Link className="advisor-text-button" to={`/courses/${course.course_id}`}>Course details</Link>
                <button type="button" className="advisor-outline-button advisor-add-course" disabled={Boolean(operation) || planner.loading || Boolean(planner.error)}
                  onClick={() => addCourse(suggestion)}>{operation === suggestion.code ? 'Saving…' : '＋ Add to draft'}</button>
              </div>
            </article>;
          })}
        </div>}
      <Link className="advisor-text-button" to="/degree-audit">View Degree Audit</Link>
    </section>

    <section className="advisor-panel advisor-draft" aria-labelledby="advisor-draft-title">
      <div className="advisor-draft-heading">
        <h2 id="advisor-draft-title">Next semester draft</h2>
        {!planner.loading && !planner.error && <p className="advisor-draft-summary">{draftCredits(draft.courses)} planned credits</p>}
      </div>
      {planner.loading ? <p className="advisor-state" role="status">Loading saved drafts…</p>
        : planner.error ? <p className="advisor-state" role="alert">{planner.error}</p> : <>
        <label className="advisor-catalog-control advisor-draft-selector">Draft semester
          <select value={selectedId} disabled={Boolean(operation)} onChange={(event) => { setSelectedId(event.target.value); setMessage(''); }}>
            {planner.semesters.map((semester, index) => <option key={semester.id} value={semester.id}>
              Semester {index + 1}{semester.term_name ? ` · ${semester.term_name}` : ' · All catalog courses'}
            </option>)}
            <option value="">New catalog draft</option>
          </select>
        </label>
        {!draft.courses.length && <p className="advisor-state">Add a suggested course to save it in Planner.</p>}
        {draft.courses.length > 0 && <ul className="advisor-draft-courses" role="list">{draft.courses.map((course) => <li key={course.course_id}>
          <Link className="advisor-text-button" to={`/courses/${course.course_id}`}>{course.course_code} · {course.course_title}</Link>
          <span>{draftCredits([course])} credits</span>
        </li>)}</ul>}
        <p className="advisor-panel-intro">Use Planner to choose sections and check schedule conflicts.</p>
      </>}
      <p className="advisor-draft-feedback" role="status">{message}</p>
      <div className="advisor-draft-actions">
        <Link className="advisor-primary-button" to="/planner">Open Planner</Link>
        <button className="advisor-text-button" type="button" disabled={Boolean(operation) || planner.loading}
          onClick={() => { setPlanner((current) => ({ ...current, loading: true })); setAttempt((value) => value + 1); }}>
          Reload saved drafts
        </button>
      </div>
    </section>
  </div>;
}

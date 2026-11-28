import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { supabase } from "../lib/supabaseClient.js";
import "../styles/Planner.css";
import GeneratedSchedules from "../components/GeneratedSchedules.jsx";
import { deleteSemester, loadPlanner, saveSemester } from "../lib/studentPlannerApi.js";

const PAGE_SIZE = 25;

function creditLabel(min, max) {
  return min === max ? `${min}` : `${min}–${max}`;
}

function CoursePicker({ term, selected, onSelect, onClose, triggerRef }) {
  const dialog = useRef(null);
  const termId = term?.term_id;
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState({ loading: true });

  useEffect(() => {
    const element = dialog.current;
    const trigger = triggerRef.current;
    element.showModal();
    return () => {
      element.close();
      if (trigger?.isConnected) trigger.focus();
    };
  }, [triggerRef]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setResult({ loading: true });
      try {
        // Query courses as the parent so multiple sections never duplicate a course.
        const columns = "course_id, course_code, course_title, min_credit_hours, max_credit_hours";
        let query = supabase.from("course").select(
          termId ? `${columns}, coursesection!inner(term_id)` : columns,
          { count: "exact" }
        );
        if (termId) query = query.eq("coursesection.term_id", termId);
        const safeSearch = search.replace(/[^a-zA-Z0-9\s-]/g, "").trim();
        if (safeSearch) {
          query = query.or(`course_code.ilike.%${safeSearch}%,course_title.ilike.%${safeSearch}%`);
        }
        const { data, error, count } = await query.order("course_code").order("course_id")
          .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).abortSignal(controller.signal);
        if (error) throw error;
        if (!controller.signal.aborted) setResult({ loading: false, courses: data ?? [], total: count ?? 0 });
      } catch {
        if (!controller.signal.aborted) setResult({ loading: false, error: true });
      }
    }
    load();
    return () => controller.abort();
  }, [termId, search, page, attempt]);

  return (
    <dialog className="planner-course-dialog" ref={dialog} aria-labelledby="course-picker-title" onCancel={onClose}>
      <div className="planner-dialog-heading">
        <h2 id="course-picker-title">Add course · {term?.term_name || "All catalog courses"}</h2>
        <button type="button" className="planner-secondary-button" onClick={onClose}>Close</button>
      </div>
      <p>{term ? "Search courses with recorded offerings in this semester." : "Browse the course catalog. Semester availability has not been verified."} Adding a course does not enroll you.</p>
      <form className="planner-search" onSubmit={(event) => {
        event.preventDefault();
        setSearch(input.trim());
        setPage(0);
      }}>
        <label htmlFor="planner-course-search">Course code or title</label>
        <div>
          <input id="planner-course-search" type="search" value={input} onChange={(event) => setInput(event.target.value)} />
          <button className="planner-primary-button" type="submit">Search</button>
        </div>
      </form>
      <div aria-busy={result.loading}>
        {result.loading ? <p role="status">Loading courses…</p> : result.error ? (
          <div className="planner-alert" role="alert">
            <p>Unable to load course offerings. Please try again.</p>
            <button className="planner-secondary-button" type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
          </div>
        ) : (
          <>
            <p role="status">{result.total} courses found</p>
            {!result.courses.length && <p>{search ? "No courses match your search in this selection." : "No courses are listed for this selection."}</p>}
            <ul className="planner-picker-results" role="list">
              {result.courses.map((course) => {
                const added = selected.some((item) => item.course_id === course.course_id);
                return (
                  <li key={course.course_id}>
                    <div className="planner-picker-course"><Link className="planner-course-link"
                      to={`/courses/${course.course_id}`} target="_blank" rel="noopener noreferrer">
                      <strong>{course.course_code}</strong> — {course.course_title}
                      <span className="planner-course-link-hint">View details (opens in a new tab)</span>
                    </Link>
                      <p>{creditLabel(course.min_credit_hours, course.max_credit_hours)} credits</p>
                    </div>
                    <button type="button" className="planner-secondary-button" disabled={added}
                      aria-label={`${added ? "Added" : "Add"} ${course.course_code}`}
                      onClick={() => onSelect(course)}>{added ? "Added" : "Add"}</button>
                  </li>
                );
              })}
            </ul>
            {result.total > PAGE_SIZE && (
              <div className="planner-pagination">
                <button className="planner-search-previous-button" type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                <span>Page {page + 1} of {Math.ceil(result.total / PAGE_SIZE)}</span>
                <button className="planner-search-next-button" type="button" disabled={(page + 1) * PAGE_SIZE >= result.total} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </>
        )}
      </div>
    </dialog>
  );
}

function SemesterColumn({ semester, number, terms, loadingTerms, termError, onSaved, onRemoved }) {
  const { id: semesterId, revision, generated_summary: generatedSummary } = semester;
  const [termId, setTermId] = useState(semester.term_id == null ? "catalog" : String(semester.term_id));
  const activeTerm = terms.find((item) => String(item.term_id) === termId);
  const term = activeTerm ?? (termId === "catalog" ? null : {
    term_id: Number(termId), term_name: semester.term_name || `Term ${termId}`,
  });
  const title = `Semester ${number}`;
  const [courses, setCourses] = useState(semester.courses);
  const [picking, setPicking] = useState(false);
  const [operation, setOperation] = useState("");
  const [saveError, setSaveError] = useState("");
  const pickerTrigger = useRef(null);
  const dirty = termId !== (semester.term_id == null ? "catalog" : String(semester.term_id)) || courses !== semester.courses;

  async function persist() {
    setOperation("saving");
    setSaveError("");
    try {
      const saved = await saveSemester({ ...semester, courses, term_id: term?.term_id ?? null, term_name: term?.term_name ?? null });
      setCourses(saved.courses);
      onSaved(saved);
    } catch (error) {
      setSaveError(error.message);
    } finally {
      setOperation("");
    }
  }

  async function remove() {
    if (revision && !window.confirm(`Delete ${title} and its courses?`)) return;
    setOperation("deleting");
    setSaveError("");
    try {
      if (revision) await deleteSemester(semesterId, revision);
      onRemoved(semesterId);
    } catch (error) {
      setSaveError(error.message);
      setOperation("");
    }
  }

  const min = courses.reduce((sum, course) => sum + Number(course.min_credit_hours), 0);
  const max = courses.reduce((sum, course) => sum + Number(course.max_credit_hours), 0);

  const saved = revision && !dirty;

  return (
    <section className="planner-semester-card" aria-labelledby={`planner-title-${semesterId}`}>
      <header className="planner-semester-heading">
        <div>
          <h3 id={`planner-title-${semesterId}`}>{title}</h3>
          <p className="planner-semester-status" data-saved={Boolean(saved)} role="status">
            {operation === "saving" ? "Saving semester…" : operation === "deleting" ? "Deleting semester…"
              : saved ? "Semester saved." : "Unsaved semester changes."}
          </p>
        </div>
        <p className="planner-credit-total">{creditLabel(min, max)} planned credits</p>
      </header>
      <label className="planner-term-label" htmlFor={`planner-term-${semesterId}`}>Course availability</label>
      <select id={`planner-term-${semesterId}`} value={termId}
        disabled={Boolean(operation) || courses.length > 0}
        aria-describedby={`planner-term-help-${semesterId}`}
        onChange={(event) => setTermId(event.target.value)}>
        <option value="catalog">All catalog courses (no term)</option>
        {term && !activeTerm && <option value={termId}>{term.term_name}</option>}
        {terms.map((item) => <option key={item.term_id} value={item.term_id}>{item.term_name}</option>)}
      </select>
      <p id={`planner-term-help-${semesterId}`} className="planner-term-help">
        {courses.length ? "Remove courses before changing course availability." : loadingTerms
          ? "Loading term options. You can browse the catalog now." : termError
          ? "Term options could not be loaded. Catalog search is available." : !terms.length
          ? "No active terms are available. Catalog courses may not be offered in your planned semester."
          : "Choose a term to see its offerings, or browse the full catalog."}
      </p>

      {generatedSummary && (
        <div className="planner-generated-summary">
          <p>
            Created from a generated schedule
            {generatedSummary.status === "TENTATIVE"
              ? " with unresolved checks."
              : "."}
            {" "}Manual changes are not automatically checked for conflicts
            or prerequisites.
          </p>
          {generatedSummary.unscheduled_required.length > 0 && (
            <p>
              Required courses left out of the original option:{" "}
              {generatedSummary.unscheduled_required.join(", ")}
            </p>
          )}
          {generatedSummary.warnings.length > 0 && (
            <details>
              <summary>Original schedule warnings</summary>
              <ul>
                {generatedSummary.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {!courses.length && <p className="planner-empty">No courses added yet.</p>}
      <ul className="planner-course-list" role="list">
        {courses.map((course) => (
          <li className="planner-course-slot" key={course.course_id}>
            <div className="planner-course-title"><strong>{course.course_code}</strong><span>{course.course_title}</span></div>
            <p>{creditLabel(course.min_credit_hours, course.max_credit_hours)} credits</p>
            {course.selected_section && (
              <p className="planner-term-help">
                Section {course.selected_section.section_number}
                {course.selected_section.crn != null && ` · CRN ${course.selected_section.crn}`}
              </p>
            )}
            <button type="button" className="planner-remove-button" disabled={Boolean(operation)} aria-label={`Remove ${course.course_code} from ${title}`}
              onClick={() => setCourses(courses.filter((item) => item.course_id !== course.course_id))}>Remove</button>
          </li>
        ))}
      </ul>
      <div className="planner-semester-actions">
        <button ref={pickerTrigger} className="planner-secondary-button" type="button" disabled={Boolean(operation)} onClick={() => setPicking(true)}>Add Course</button>
        <button className="planner-primary-button" type="button" disabled={Boolean(operation) || (revision && !dirty)}
          onClick={persist}>{operation === "saving" ? "Saving…" : "Save Semester"}</button>
        <button className="planner-remove-button" type="button" disabled={Boolean(operation)}
          onClick={remove}>{revision ? "Delete Semester" : "Discard Semester"}</button>
      </div>
      {saveError && <p className="planner-alert" role="alert">{saveError}</p>}
      {picking && <CoursePicker term={term} selected={courses} triggerRef={pickerTrigger} onClose={() => setPicking(false)}
        onSelect={(course) => setCourses((current) => current.some((item) => item.course_id === course.course_id)
          ? current : [...current, {
            course_id: course.course_id,
            course_code: course.course_code,
            course_title: course.course_title ?? "",
            min_credit_hours: Number(course.min_credit_hours),
            max_credit_hours: Number(course.max_credit_hours),
            selected_section: null,
          }])} />}
    </section>
  );
}

function Planner() {
  const [terms, setTerms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [semesters, setSemesters] = useState([]);
  const [preferences, setPreferences] = useState(null);
  const [planState, setPlanState] = useState({ loading: true });
  const [planAttempt, setPlanAttempt] = useState(0);
  const imports = useRef(new WeakMap());

  useEffect(() => {
    const controller = new AbortController();
    loadPlanner(controller.signal).then((record) => {
      if (controller.signal.aborted) return;
      setSemesters(record.semesters);
      setPreferences(record.preferences);
      setPlanState({ loading: false });
    }).catch((error) => {
      if (!controller.signal.aborted) setPlanState({ loading: false, error: error.message });
    });
    return () => controller.abort();
  }, [planAttempt]);

  function upsertSemester(saved) {
    setSemesters((current) => current.some((semester) => semester.id === saved.id)
      ? current.map((semester) => semester.id === saved.id ? saved : semester) : [...current, saved]);
  }

  async function handleUseGeneratedSchedule({ termId, option, creditHours, handoffWarnings }) {
    let draft = imports.current.get(option);
    if (!draft) {
      draft = {
        id: crypto.randomUUID(),
        term_id: Number(termId),
        term_name: terms.find((term) => String(term.term_id) === termId)?.term_name ?? null,
        courses: Object.entries(option.sections).map(([code, section]) => ({
          course_id: section.course_id,
          course_code: code,
          course_title: section.course_title ?? "",
          min_credit_hours: creditHours[code],
          max_credit_hours: creditHours[code],
          selected_section: {
            section_id: section.section_id, section_number: section.section_number,
            crn: section.crn,
          },
        })),
        generated_summary: {
          status: option.status,
          warnings: [...(option.warnings ?? []), ...handoffWarnings],
          unscheduled_required: option.unscheduled_required ?? [],
        },
      };
      imports.current.set(option, draft);
    }
    upsertSemester(await saveSemester(draft));
  }

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError(false);
      try {
        const allTerms = [];
        // Read every page rather than silently relying on the API's row limit.
        for (let start = 0; ; start += 100) {
          const { data, error } = await supabase.from("term")
            .select("term_id, term_name, start_date").eq("active_status", true)
            .order("start_date", { nullsFirst: false }).order("term_id")
            .range(start, start + 99).abortSignal(controller.signal);
          if (error) throw error;
          if (controller.signal.aborted) return;
          allTerms.push(...(data ?? []));
          if (!data || data.length < 100) break;
        }
        setTerms(allTerms);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    return () => controller.abort();
  }, [attempt]);

  return (
    <main className="planner-page">
      <header className="planner-heading">
        <div>
          <h1>Planner</h1>
          <p>Organize future semesters using recorded course offerings.</p>
        </div>
        <a className="planner-hero-link" href="#semester-plan">View semester plan <span aria-hidden="true">↓</span></a>
      </header>

      {!planState.loading && !planState.error && <GeneratedSchedules terms={terms} loadingTerms={loading}
        initialPreferences={preferences} onUseSchedule={handleUseGeneratedSchedule} />}

      <section className="planner-plan" aria-labelledby="semester-plan">
        <div className="planner-plan-heading">
          <div>
            <h2 id="semester-plan" tabIndex={-1}>Semester Plan</h2>
            {!planState.loading && !planState.error && <p>Save a semester to access it the next time you sign in.</p>}
          </div>
          {!planState.loading && !planState.error && (
            <button className="planner-secondary-button" type="button" onClick={() => setSemesters((current) => [...current, {
              id: crypto.randomUUID(), term_id: null, term_name: null, courses: [], generated_summary: null,
            }])}>Add Semester</button>
          )}
        </div>
        {planState.loading ? <p className="planner-state" role="status">Loading saved semesters…</p> : planState.error ? (
          <div className="planner-alert" role="alert">
            <p>{planState.error}</p>
            <button className="planner-secondary-button" type="button" onClick={() => {
              setPlanState({ loading: true });
              setPlanAttempt((value) => value + 1);
            }}>Retry loading semesters</button>
          </div>
        ) : !semesters.length && <p className="planner-empty">No saved semester plans yet.</p>}
        {loading ? <p className="planner-state" role="status">Loading available terms…</p> : error ? (
          <div className="planner-alert" role="alert">
            <p>Unable to load terms.</p>
            <button className="planner-secondary-button" type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
          </div>
        ) : !terms.length ? <p className="planner-state">No active terms are listed. You can still add courses from the catalog. Semester availability is unverified.</p> : null}
        {semesters.length > 0 && (
          <div className="planner-semester-grid">
            {semesters.map((semester, index) => (
              <SemesterColumn
                key={semester.id}
                semester={semester}
                number={index + 1}
                onSaved={upsertSemester}
                onRemoved={(id) => setSemesters((current) => current.filter((plan) => plan.id !== id))}
                terms={terms}
                loadingTerms={loading}
                termError={error}
              />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export default Planner;

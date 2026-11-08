import { useEffect, useRef, useState } from "react";
import { supabase } from "../lib/supabaseClient.js";
import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/Planner.css";

const PAGE_SIZE = 25;

function creditLabel(min, max) {
  return min === max ? `${min}` : `${min}–${max}`;
}

function CoursePicker({ term, selected, onSelect, onClose }) {
  const dialog = useRef(null);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState({ loading: true });

  useEffect(() => {
    const element = dialog.current;
    element.showModal();
    return () => element.close();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setResult({ loading: true });
      try {
        // Query courses as the parent so multiple sections never duplicate a course.
        let query = supabase.from("course").select(
          "course_id, course_code, course_title, min_credit_hours, max_credit_hours, coursesection!inner(term_id)",
          { count: "exact" }
        ).eq("coursesection.term_id", term.term_id);
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
  }, [term.term_id, search, page, attempt]);

  return (
    <dialog className="planner-course-dialog" ref={dialog} aria-labelledby="course-picker-title" onCancel={onClose}>
      <div className="planner-dialog-heading">
        <h2 id="course-picker-title">Add course · {term.term_name}</h2>
        <button type="button" className="planner-secondary-button" onClick={onClose}>Close</button>
      </div>
      <p>Search courses with recorded offerings in this semester. Adding a course does not enroll you.</p>
      <form className="planner-search" onSubmit={(event) => {
        event.preventDefault();
        setSearch(input.trim());
        setPage(0);
      }}>
        <label htmlFor="planner-course-search">Course code or title</label>
        <div>
          <input id="planner-course-search" value={input} onChange={(event) => setInput(event.target.value)} />
          <button className="planner-primary-button" type="submit">Search</button>
        </div>
      </form>
      <div aria-busy={result.loading}>
        {result.loading ? <p role="status">Loading courses…</p> : result.error ? (
          <div role="alert">
            <p>Unable to load course offerings. Please try again.</p>
            <button type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button>
          </div>
        ) : (
          <>
            <p role="status">{result.total} courses found</p>
            {!result.courses.length && <p>{search ? "No courses match your search in this semester." : "No course offerings are listed for this semester."}</p>}
            <ul className="planner-picker-results">
              {result.courses.map((course) => {
                const added = selected.some((item) => item.course_id === course.course_id);
                return (
                  <li key={course.course_id}>
                    <div><strong>{course.course_code}</strong> — {course.course_title}
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
                <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                <span>Page {page + 1} of {Math.ceil(result.total / PAGE_SIZE)}</span>
                <button type="button" disabled={(page + 1) * PAGE_SIZE >= result.total} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </>
        )}
      </div>
    </dialog>
  );
}

function SemesterColumn({ semesterId, terms, loadingTerms, termError }) {
  const [termId, setTermId] = useState("");
  const term = terms.find((item) => String(item.term_id) === termId);
  const title = `Semester ${semesterId}`;
  const [courses, setCourses] = useState([]);
  const [picking, setPicking] = useState(false);
  const min = courses.reduce((sum, course) => sum + Number(course.min_credit_hours), 0);
  const max = courses.reduce((sum, course) => sum + Number(course.max_credit_hours), 0);

  return (
    <section className="planner-semester-card" aria-label={title}>
      <h2>{title}</h2>
      <label className="planner-term-label" htmlFor={`planner-term-${semesterId}`}>Term</label>
      <select id={`planner-term-${semesterId}`} value={termId}
        disabled={loadingTerms || termError || !terms.length || courses.length > 0}
        aria-describedby={`planner-term-help-${semesterId}`}
        onChange={(event) => setTermId(event.target.value)}>
        <option value="">Choose a term</option>
        {terms.map((item) => <option key={item.term_id} value={item.term_id}>{item.term_name}</option>)}
      </select>
      <p id={`planner-term-help-${semesterId}`} className="planner-term-help">
        {courses.length ? "Remove courses before changing the term." : "Choose a term to search its course offerings."}
      </p>
      <p>{creditLabel(min, max)} planned credits</p>
      {!courses.length && <p className="planner-empty">No courses added yet.</p>}
      <ul className="planner-course-list">
        {courses.map((course) => (
          <li className="planner-course-slot planner-course-slot-filled" key={course.course_id}>
            <div className="planner-course-title"><strong>{course.course_code}</strong><span>{course.course_title}</span></div>
            <p>{creditLabel(course.min_credit_hours, course.max_credit_hours)} credits</p>
            <button type="button" className="planner-remove-button" aria-label={`Remove ${course.course_code} from ${title}`}
              onClick={() => setCourses(courses.filter((item) => item.course_id !== course.course_id))}>Remove</button>
          </li>
        ))}
      </ul>
      <div className="planner-semester-actions">
        <button className="planner-secondary-button" type="button" disabled={!term} onClick={() => setPicking(true)}>Add Course</button>
        <button className="planner-primary-button" type="button" disabled aria-describedby={`planner-save-help-${semesterId}`}>Save Semester</button>
      </div>
      <p id={`planner-save-help-${semesterId}`} className="planner-term-help">Saving semesters is not available yet.</p>
      {picking && term && <CoursePicker term={term} selected={courses} onClose={() => setPicking(false)}
        onSelect={(course) => setCourses((current) => current.some((item) => item.course_id === course.course_id) ? current : [...current, course])} />}
    </section>
  );
}

function Planner() {
  const [terms, setTerms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [semesters, setSemesters] = useState([1]);

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
      <div className="planner-heading">
        <h1>Planner</h1>
        <p>Organize future semesters using recorded course offerings.</p>
      </div>
      <DashboardCard title="Semester Plan">
        <p>Your plan is temporary. Refreshing or leaving this page clears your selections.</p>
        <button className="planner-primary-button" type="button"
          onClick={() => setSemesters((current) => [...current, current.length + 1])}>Add Semester</button>
        {loading ? <p role="status">Loading available terms…</p> : error ? (
          <div role="alert"><p>Unable to load terms.</p><button type="button" onClick={() => setAttempt(attempt + 1)}>Try again</button></div>
        ) : !terms.length ? <p>No active terms are listed.</p> : null}
      </DashboardCard>
      <div className="planner-semester-grid">
        {semesters.map((id) => <SemesterColumn semesterId={id} terms={terms} loadingTerms={loading} termError={error} key={id} />)}
      </div>
    </main>
  );
}

export default Planner;

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
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
        const columns = "course_id, course_code, course_title, min_credit_hours, max_credit_hours";
        let query = supabase.from("course").select(
          term ? `${columns}, coursesection!inner(term_id)` : columns,
          { count: "exact" }
        );
        if (term) query = query.eq("coursesection.term_id", term.term_id);
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
  }, [term, search, page, attempt]);

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
            {!result.courses.length && <p>{search ? "No courses match your search in this selection." : "No courses are listed for this selection."}</p>}
            <ul className="planner-picker-results">
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

function SemesterColumn({ semesterId, terms, loadingTerms, termError }) {
  const [termId, setTermId] = useState("catalog");
  const term = terms.find((item) => String(item.term_id) === termId);
  const title = `Semester ${semesterId}`;
  const [courses, setCourses] = useState([]);
  const [picking, setPicking] = useState(false);
  const min = courses.reduce((sum, course) => sum + Number(course.min_credit_hours), 0);
  const max = courses.reduce((sum, course) => sum + Number(course.max_credit_hours), 0);

  return (
    <section className="planner-semester-card" aria-label={title}>
      <h2>{title}</h2>
      <label className="planner-term-label" htmlFor={`planner-term-${semesterId}`}>Course availability</label>
      <select id={`planner-term-${semesterId}`} value={termId}
        disabled={courses.length > 0}
        aria-describedby={`planner-term-help-${semesterId}`}
        onChange={(event) => setTermId(event.target.value)}>
        <option value="catalog">All catalog courses (no term)</option>
        {terms.map((item) => <option key={item.term_id} value={item.term_id}>{item.term_name}</option>)}
      </select>
      <p id={`planner-term-help-${semesterId}`} className="planner-term-help">
        {courses.length ? "Remove courses before changing course availability." : loadingTerms
          ? "Loading term options. You can browse the catalog now." : termError
          ? "Term options could not be loaded. Catalog search is available." : !terms.length
          ? "No active terms are available. Catalog courses may not be offered in your planned semester."
          : "Choose a term to see its offerings, or browse the full catalog."}
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
        <button className="planner-secondary-button" type="button" onClick={() => setPicking(true)}>Add Course</button>
        <button className="planner-primary-button" type="button" disabled aria-describedby={`planner-save-help-${semesterId}`}>Save Semester</button>
      </div>
      <p id={`planner-save-help-${semesterId}`} className="planner-term-help">Saving semesters is not available yet.</p>
      {picking && <CoursePicker term={term} selected={courses} onClose={() => setPicking(false)}
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
        ) : !terms.length ? <p>No active terms are listed. You can still add courses from the catalog; semester availability is unverified.</p> : null}
      </DashboardCard>
      <div className="planner-semester-grid">
        {semesters.map((id) => <SemesterColumn semesterId={id} terms={terms} loadingTerms={loading} termError={error} key={id} />)}
      </div>
    </main>
  );
}

export default Planner;

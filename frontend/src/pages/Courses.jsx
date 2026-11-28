import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { supabase } from "../lib/supabaseClient.js";
import "../styles/Courses.css";

const PAGE_SIZE = 25;

function CourseRow({ course }) {
  return (
    <li className="catalog-result">
      <div className="catalog-result-meta">
        <span className="catalog-code">{course.course_code}</span>
        {!course.active_status && <span className="catalog-inactive">Inactive</span>}
        <span className="catalog-result-credits">
          {course.min_credit_hours}
          {course.min_credit_hours !== course.max_credit_hours && `–${course.max_credit_hours}`} credits
        </span>
      </div>
      <h3 className="catalog-result-title">
        <Link className="catalog-result-link" to={`/courses/${course.course_id}`} state={{ fromCourses: true }}
          aria-label={`${course.course_title} (${course.course_code})`}>
          <span>{course.course_title}</span>
          <span aria-hidden="true">→</span>
        </Link>
      </h3>
      <p>{course.description || "No description available."}</p>
    </li>
  );
}

function Courses() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [activeOnly, setActiveOnly] = useState(true);

  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function fetchCourses() {
      setLoading(true);
      setError(null);

      let query = supabase
        .from("course")
        .select(
          "course_id, course_code, course_title, description, min_credit_hours, max_credit_hours, active_status",
          { count: "exact" }
        );

      // Filter for active courses
      if (activeOnly) {
        query = query.eq("active_status", true);
      }

      // Search course code or title
      if (searchTerm) {
        const safeSearch = searchTerm
          .replace(/[^a-zA-Z0-9\s-]/g, "")
          .trim();

        if (safeSearch) {
          query = query.or(
            `course_code.ilike.%${safeSearch}%,course_title.ilike.%${safeSearch}%`
          );
        }
      }

      // Sort and paginate
      const from = page * PAGE_SIZE;

      query = query
        .order("course_code", { ascending: true })
        .order("course_id", { ascending: true })
        .range(from, from + PAGE_SIZE - 1);

      const { data, error, count } = await query;

      if (cancelled) return;

      if (error) {
        console.error("Supabase error:", error);
        setError(error.message);
        setCourses([]);
        setTotal(0);
      } else {
        setCourses(data ?? []);
        setTotal(count ?? 0);
      }

      setLoading(false);
    }

    fetchCourses();

    return () => {
      cancelled = true;
    };
  }, [page, searchTerm, activeOnly]);

  function handleSearch(event) {
    event.preventDefault();
    setPage(0);
    setSearchTerm(searchInput.trim());
  }

  function handleExplore() {
    setSearchInput("");
    setSearchTerm("");
    setActiveOnly(true);
    setPage(0);
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="catalog-page">
      <header className="catalog-hero">
        <h1>Courses</h1>
        <p>Explore the catalog and check course requirements before planning your semester.</p>
      </header>

      <form className="catalog-panel catalog-search" role="search" aria-labelledby="catalog-search-title" onSubmit={handleSearch}>
        <h2 id="catalog-search-title">Find a course</h2>
        <div className="catalog-search-controls">
          <div className="catalog-search-field">
            <label htmlFor="catalog-search-input">Course code or title</label>
            <div>
              <input
                className="catalog-input"
                id="catalog-search-input"
                type="search"
                placeholder="For example, CS 110"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
              <button className="catalog-button catalog-button-primary" type="submit">Search</button>
            </div>
          </div>
          <div className="catalog-filter-controls">
            <button
              className="catalog-button catalog-filter"
              type="button"
              aria-pressed={activeOnly}
              onClick={() => {
                setActiveOnly(!activeOnly);
                setPage(0);
              }}
            >Active only</button>
            <button className="catalog-button" type="button" onClick={handleExplore}>Reset filters</button>
          </div>
        </div>
      </form>

      <section className="catalog-panel catalog-results" aria-labelledby="catalog-results-title">
        <header className="catalog-results-heading">
          <h2 id="catalog-results-title">Course results</h2>
          <span className="catalog-count" role="status">
            {loading ? "Loading courses…" : error ? "" : `${total} ${total === 1 ? "course" : "courses"} found`}
          </span>
        </header>
        {loading ? (
          <div className="catalog-result-placeholder" aria-hidden="true"><span /><span /></div>
        ) : error ? (
          <p className="catalog-notice catalog-warning" role="alert">Error loading courses: {error}</p>
        ) : (
          <>
            {courses.length > 0 ? (
              <ul className="catalog-results-list" role="list">
                {courses.map((course) => <CourseRow course={course} key={course.course_id} />)}
              </ul>
            ) : (
              <p className="catalog-notice">No courses found. Try a different search or include inactive courses.</p>
            )}

            {totalPages > 1 && (
              <nav className="catalog-pagination" aria-label="Course results pages">
                <button
                  className="catalog-button"
                  type="button"
                  disabled={page === 0}
                  onClick={() => setPage(page - 1)}
                >Previous</button>
                <span>Page {page + 1} of {totalPages}</span>
                <button
                  className="catalog-button"
                  type="button"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage(page + 1)}
                >Next</button>
              </nav>
            )}
          </>
        )}
      </section>
    </main>
  );
}

export default Courses;

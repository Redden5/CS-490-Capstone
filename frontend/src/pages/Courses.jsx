
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { supabase } from "../lib/supabaseClient.js";
import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/Courses.css";

const PAGE_SIZE = 25;

function CourseRow({ course }) {
  return (
    <div className="course-result-row">
      <div className="course-result-title">
        <Link to={`/courses/${course.course_id}`} state={{ fromCourses: true }}>
          <strong>{course.course_code}</strong> — {course.course_title}
          <span className="course-detail-link-label">View details →</span>
        </Link>
      </div>

      <p>{course.description || "No description available."}</p>

      <p>
        <strong>Credits:</strong> {course.min_credit_hours}
        {course.min_credit_hours !== course.max_credit_hours &&
          ` - ${course.max_credit_hours}`}
      </p>
    </div>
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
    <main className="courses-page">
      <div className="courses-heading">
        <h1>Courses</h1>
        <p>Browse course information and prerequisite details.</p>
      </div>

      <form
        className="course-search-section"
        onSubmit={handleSearch}
      >
        <input
          className="course-search-input"
          type="text"
          placeholder="Search courses"
          aria-label="Search courses"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />

        <button className="course-search-button"type="submit">
          <span>Search</span>
        </button>

        <button
          className="course-filter-button"
          type="button"
          aria-pressed={activeOnly}
          onClick={() => {
            setActiveOnly(!activeOnly);
            setPage(0);
          }}
        >
          {activeOnly ? "Active Only" : "All Courses"}
        </button>

        <button
          className="course-explore-button"
          type="button"
          onClick={handleExplore}
        >
          Explore Courses
        </button>
      </form>

      <DashboardCard title="Course Results">
        {loading ? (
          <p>Loading courses...</p>
        ) : error ? (
          <p role="alert">Error loading courses: {error}</p>
        ) : (
          <>
            <p>{total} courses found</p>

            <div className="course-results-list">
              {courses.length > 0 ? (
                courses.map((course) => (
                  <CourseRow
                    course={course}
                    key={course.course_id}
                  />
                ))
              ) : (
                <p>No courses found.</p>
              )}
            </div>

            {totalPages > 1 && (
              <div className="course-pagination">
                <button
                  type="button"
                  disabled={page === 0}
                  onClick={() => setPage(page - 1)}
                >
                  Previous
                </button>

                <span>
                  Page {page + 1} of {totalPages}
                </span>

                <button
                  type="button"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </DashboardCard>
    </main>
  );
}

export default Courses;

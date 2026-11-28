import { catalogCreditLabel } from '../lib/planningPresentation.js';
import { searchCourses } from '../lib/courseSearch.js';
import { useCourseSearch } from '../lib/useCourseSearch.js';
import PageHeading from '../components/PageHeading.jsx';

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

      <p>{catalogCreditLabel(course.min_credit_hours, course.max_credit_hours)}</p>
    </div>
  );
}

function Courses() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const { input: searchInput, setInput: setSearchInput, search, page, setPage, submit: handleSearch, reset } = useCourseSearch();
  const [activeOnly, setActiveOnly] = useState(true);

  const [total, setTotal] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function fetchCourses() {
      setLoading(true);
      setError(null);

      try {
        const { data, count } = await searchCourses(supabase, {
          term: search.term, page, pageSize: PAGE_SIZE, activeOnly, signal: controller.signal,
        });
        if (!controller.signal.aborted) { setCourses(data); setTotal(count); }
      } catch {
        if (!controller.signal.aborted) {
          setError('Course search is unavailable. Please try again.'); setCourses([]); setTotal(0);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    fetchCourses();

    return () => {
      controller.abort();
    };
  }, [page, search, activeOnly]);

  function handleExplore() {
    reset();
    setActiveOnly(true);
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="courses-page app-page">
      <PageHeading eyebrow="Your course catalog" title="Courses" description="Browse course descriptions, prerequisites, and recorded offerings." />

      <form
        className="course-search-section"
        onSubmit={handleSearch}
      >
        <input
          className="course-search-input"
          type="text"
          placeholder="CS, CS120, or a course title"
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
          Reset filters
        </button>
      </form>

      <DashboardCard title="Course Results">
        {loading ? (
          <p>Loading courses...</p>
        ) : error ? (
          <p role="alert">Error loading courses: {error}</p>
        ) : (
          <>
            <p>{total} {total === 1 ? 'course' : 'courses'} found</p>

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
                  className="course-search-previous-button"
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
                  className="course-search-next-button"
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

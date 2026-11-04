
import { useEffect, useState } from "react";
import { supabase } from "../lib/supabaseClient";

const PAGE_SIZE = 25;

function CourseCatalog() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function fetchCourses() {
      setLoading(true);
      setError(null);

      const from = page * PAGE_SIZE;
      const to = from + PAGE_SIZE - 1;

      const { data, error, count } = await supabase
        .from("course")
        .select(
          "course_id, course_code, course_title, description, min_credit_hours, max_credit_hours, active_status",
          { count: "exact" }
        )
        .order("course_code", { ascending: true })
        .order("course_id", { ascending: true })
        .range(from, to);

      if (cancelled) return;

      if (error) {
        console.error("Error fetching courses:", error);
        setError(error.message);
        setCourses([]);
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
  }, [page]);

  if (loading) {
    return <p>Loading courses from Supabase...</p>;
  }

  if (error) {
    return (
      <div role="alert">
        <h3>Unable to load courses</h3>
        <p>{error}</p>
      </div>
    );
  }

  return (
    <section className="course-catalog">
      <h2>Available Courses</h2>

      <p>Total courses: {total}</p>

      {courses.length === 0 && (
        <p>No courses found.</p>
      )}

      <div className="course-list">
        {courses.map((course) => (
          <article
            className="course-card"
            key={course.course_id}
          >
            <h3>
              {course.course_code} - {course.course_title}
            </h3>

            <p>
              <strong>Credits:</strong>{" "}
              {course.min_credit_hours ===
              course.max_credit_hours
                ? course.min_credit_hours
                : `${course.min_credit_hours} - ${course.max_credit_hours}`}
            </p>

            <p>
              <strong>Status:</strong>{" "}
              {course.active_status
                ? "Active"
                : "Inactive"}
            </p>

            <p>
              {course.description ||
                "No description available."}
            </p>
          </article>
        ))}
      </div>

      <div className="course-pagination">
        <button
          type="button"
          disabled={page === 0}
          onClick={() => setPage(page - 1)}
        >
          Previous
        </button>

        <span> Page {page + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))} </span>

        <button
          type="button"
          disabled={(page + 1) * PAGE_SIZE >= total}
          onClick={() => setPage(page + 1)}
        >
          Next
        </button>
      </div>
    </section>
  );
}

export default CourseCatalog;

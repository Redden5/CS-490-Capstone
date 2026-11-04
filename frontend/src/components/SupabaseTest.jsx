
import { useEffect, useState } from "react";
import { supabase } from "../lib/supabaseClient";

function SupabaseTest() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchCourses() {
      const { data, error } = await supabase
        .from("course")
        .select("course_id, course_code, course_title")
        .limit(10);

      if (error) {
        console.error("Supabase error:", error);
        setError(error.message);
      } else {
        setCourses(data);
      }

      setLoading(false);
    }

    fetchCourses();
  }, []);

  if (loading) return <p>Loading courses...</p>;

  if (error) return <p>Database error: {error}</p>;

  return (
    <div>
      <h2>Supabase Connection Test</h2>
      <p>Courses retrieved: {courses.length}</p>

      {courses.map((course) => (
        <p key={course.course_id}>
          {course.course_code} - {course.course_title}
        </p>
      ))}
    </div>
  );
}

export default SupabaseTest;

import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/Courses.css";

const courses = [
  {
    code: "CS 490",
    name: "Capstone",
    description: "Senior capstone course",
  },
  {
    code: "CS 360",
    name: "Automata & Formal Languages",
    description: "Theory / formal languages course",
  },
  {
    code: "STA 445",
    name: "Applied Statistics",
    description: "Statistics course",
  },
];

function CourseRow({ course }) {
  return (
    <div className="course-result-row">
      <div className="course-result-title">
        <strong>{course.code}</strong>
        <span>{course.name}</span>
      </div>
      <p>{course.description}</p>
    </div>
  );
}

function Courses() {
  return (
    <main className="courses-page">
      <div className="courses-heading">
        <h1>Courses</h1>
        <p>Browse course information and prerequisite details.</p>
      </div>

      <section className="course-search-section">
        <input
          className="course-search-input"
          type="text"
          placeholder="Search courses"
          aria-label="Search courses"
        />

        <button className="course-filter-button" type="button">
          Filter
        </button>

        <button className="course-explore-button" type="button">
          Explore Courses
        </button>
      </section>

      <DashboardCard title="Course Results">
        <div className="course-results-list">
          {courses.map((course) => (
            <CourseRow course={course} key={course.code} />
          ))}

          <div className="course-result-row course-result-placeholder" />
          <div className="course-result-row course-result-placeholder" />
        </div>
      </DashboardCard>
    </main>
  );
}

export default Courses;

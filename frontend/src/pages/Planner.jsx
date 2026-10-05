import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/Planner.css";

const semesterOneCourses = [
  { code: "CS 490", name: "Capstone", note: "Sample planned course" },
  { code: "CS 360", name: "Automata & Formal Languages", note: "Sample planned course" },
  { code: "STA 445", name: "Applied Statistics", note: "Sample planned course" },
];

function CourseSlot({ course }) {
  if (!course) {
    return (
      <div className="planner-course-slot planner-course-slot-empty">
        <span>Sample course option</span>
      </div>
    );
  }

  return (
    <div className="planner-course-slot planner-course-slot-filled">
      <div className="planner-course-title">
        <strong>{course.code}</strong>
        <span>{course.name}</span>
      </div>
      <p>{course.note}</p>
    </div>
  );
}

function SemesterColumn({ title, courses = [] }) {
  const slots = Array.from({ length: 4 }, (_, index) => courses[index] || null);

  return (
    <section className="planner-semester-card">
      <h2>{title}</h2>

      <div className="planner-course-list">
        {slots.map((course, index) => (
          <CourseSlot
            course={course}
            key={course ? course.code : `${title}-${index}`}
          />
        ))}
      </div>

      <button className="planner-secondary-button" type="button">
        Add Course
      </button>
    </section>
  );
}

function Planner() {
  return (
    <main className="planner-page">
      <div className="planner-heading">
        <h1>Planner</h1>
        <p>Organize future semesters and potential course schedules.</p>
      </div>

      <DashboardCard title="Semester Plan">
        <div className="planner-plan-actions">
          <button className="planner-primary-button" type="button">
            Add Semester
          </button>

          <button className="planner-secondary-button" type="button">
            Generate Potential Schedule
          </button>
        </div>
      </DashboardCard>

      <div className="planner-semester-grid">
        <SemesterColumn title="Semester 1" courses={semesterOneCourses} />
        <SemesterColumn title="Semester 2" />
        <SemesterColumn title="Future Semester" />
      </div>
    </main>
  );
}

export default Planner;

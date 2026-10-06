import { Link } from "react-router-dom";
import DashboardCard from "../components/DashboardCard.jsx";
import "../styles/DegreeAudit.css";

const requirementSections = [
  "General Education",
  "Major Requirements",
  "Supporting Courses",
  "Electives",
];

const eligibleCourses = [
  
];

function DegreeAudit() {
  return (
    <main className="degree-audit-page">
      <div className="degree-audit-heading">
        <h1>Degree Audit</h1>
        <p>Review requirements, progress, and course eligibility.</p>
      </div>

      <DashboardCard title="Degree Progress">
        <div
          className="degree-progress-track"
          role="progressbar"
          aria-label="Degree progress"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow="72"
        >
          <div className="degree-progress-fill" />
        </div>
      </DashboardCard>

      <section className="degree-audit-stats" aria-label="Degree summary">
        <DashboardCard title="Completed">
          <div className="summary-bar summary-bar-completed" aria-hidden="true" />
          <p className="summary-value">? credits</p>
        </DashboardCard>

        <DashboardCard title="In Progress">
          <div className="summary-bar summary-bar-progress" aria-hidden="true" />
          <p className="summary-value">? credits</p>
        </DashboardCard>

        <DashboardCard title="Remaining">
          <div className="summary-bar summary-bar-remaining" aria-hidden="true" />
          <p className="summary-value">? credits</p>
        </DashboardCard>
      </section>

      <section className="degree-audit-content">
        <DashboardCard title="Degree Requirements">
          <div className="requirement-list">
            {requirementSections.map((section) => (
              <button className="requirement-row" type="button" key={section}>
                <span>{section}</span>
                <span className="requirement-arrow" aria-hidden="true">
                  ›
                </span>
              </button>
            ))}
          </div>
        </DashboardCard>

        <div className="degree-audit-sidebar">
          <DashboardCard title="Course Eligibility">
            <div className="eligibility-list">
              {eligibleCourses.map((course) => (
                <div
                  className={`eligibility-item ${
                    course.highlight ? "eligible-now" : ""
                  }`}
                  key={course.code}
                >
                  <strong>{course.code}</strong>
                  <span>{course.name}</span>
                </div>
              ))}
            </div>
          </DashboardCard>

          <DashboardCard title="Quick Actions">
            <div className="degree-audit-actions">
              <button className="primary-link" type="button">
                Open Planner
              </button>
              <Link to="/courses">Explore Courses</Link>
            </div>
          </DashboardCard>
        </div>
      </section>
    </main>
  );
}

export default DegreeAudit;

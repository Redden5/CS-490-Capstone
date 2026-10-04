import { Link } from "react-router-dom";
import DashboardCard from "../components/DashboardCard.jsx";
import CurrentCoursework from "../components/CurrentCoursework.jsx";

function Dashboard() {
  return (
    <main className="dashboard">
      <h1>Dashboard</h1>
      

      <div className="dashboard-overview">
        <DashboardCard title="Degree Progress">
          
          <div className="placeholder-bars" aria-hidden="true">
            <span />
            <span />
          </div>
          <span className="sr-only">Degree progress details coming soon.</span>
        </DashboardCard>

        <DashboardCard title="Current Coursework">
          <CurrentCoursework />
        </DashboardCard>

        <DashboardCard title="Next Steps">
          <div className="placeholder-bars" aria-hidden="true">
            <span />
            <span />
          </div>
          <span className="sr-only">Next step details coming soon.</span>
        </DashboardCard>
      </div>

      <div className="dashboard-actions">
        <DashboardCard title="Recommended Next Actions">
          <div className="action-links">
            <Link to="/degree-audit">Review remaining requirements</Link>
            <Link to="/courses">Check course eligibility</Link>
            <Link to="/planner">Build a future semester plan</Link>
            <Link to="/advisor">Ask the AI Advisor</Link>
          </div>
        </DashboardCard>

        <DashboardCard title="Quick Access">
          <div className="quick-links">
            <Link className="primary-link" to="/degree-audit">Degree Audit</Link>
            <Link to="/planner">Open Planner</Link>
            <Link to="/courses">Explore Courses</Link>
            <Link to="/academic-info">Add Coursework</Link>
          </div>
        </DashboardCard>
      </div>
    </main>
  );
}

export default Dashboard;

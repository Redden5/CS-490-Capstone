import { Link, NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/degree-audit", label: "Degree Audit" },
  { to: "/planner", label: "Planner" },
  { to: "/courses", label: "Courses" },
  { to: "/advisor", label: "AI Advisor" },
];

function Header() {
  return (
    <header className="site-header">
      <Link className="brand" to="/" aria-label="Marshall Course Advisor home">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-name">MARSHALL</span>
        <span className="brand-title">Course Advisor</span>
      </Link>

      <nav className="main-nav" aria-label="Main navigation">
        
        {links.map(({ to, label }) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
      </nav>
    </header>
  );
}

export default Header;

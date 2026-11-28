import { useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/degree-audit", label: "Degree Audit" },
  { to: "/planner", label: "Planner" },
  { to: "/courses", label: "Courses" },
  { to: "/advisor", label: "AI Advisor" },
];

function Header({ onSignOut }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuToggle = useRef(null);

  function closeWithEscape(event) {
    if (event.key === 'Escape' && menuOpen) {
      setMenuOpen(false);
      menuToggle.current?.focus();
    }
  }

  return (
    <header className={`site-header${menuOpen ? " nav-open" : ""}`} onKeyDown={closeWithEscape}>
      <Link className="brand" to="/" aria-label="Marshall Course Advisor home" onClick={() => setMenuOpen(false)}>
        <img className="header-brand-icon" src="/course-advisor.svg" alt="" width="36" height="36" />
        <span className="brand-name">MARSHALL</span>
        <span className="brand-title">Course Advisor</span>
      </Link>

      <button ref={menuToggle} className="header-nav-toggle" type="button" aria-controls="main-navigation"
        aria-expanded={menuOpen} aria-label={menuOpen ? "Close navigation" : "Open navigation"}
        onClick={() => setMenuOpen((open) => !open)}>
        <span className="menu-line" aria-hidden="true" /><span className="menu-line" aria-hidden="true" />
      </button>
      <nav id="main-navigation" className="main-nav" aria-label="Main navigation">
        {links.map(({ to, label }) => (
          <NavLink key={to} to={to} end={to === "/"} onClick={() => setMenuOpen(false)}>
            {label}
          </NavLink>
        ))}
      </nav>
      <button className="header-sign-out" type="button" aria-label="Sign Out" onClick={onSignOut}>
        <span className="sign-out-label">Sign Out</span>
        <svg className="sign-out-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9 5H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M14 8l4 4-4 4M8 12h13" />
        </svg>
      </button>
    </header>
  );
}

export default Header;

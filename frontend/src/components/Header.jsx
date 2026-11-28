import { useLayoutEffect, useRef } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import marshallLogo from "../assets/marshall-header-logo.webp";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/degree-audit", label: "Degree Audit" },
  { to: "/planner", label: "Planner" },
  { to: "/courses", label: "Courses" },
  { to: "/advisor", label: "AI Advisor" },
];

function Header({ onSignOut }) {
  const { pathname } = useLocation();
  const navRef = useRef(null);
  const indicatorRef = useRef(null);

  useLayoutEffect(() => {
    const nav = navRef.current;
    const indicator = indicatorRef.current;
    const activeLink = nav.querySelector('a[aria-current="page"]');

    if (!activeLink) {
      indicator.hidden = true;
      return;
    }

    function positionIndicator() {
      const navBounds = nav.getBoundingClientRect();
      const linkBounds = activeLink.getBoundingClientRect();
      indicator.style.width = `${linkBounds.width}px`;
      indicator.style.height = `${linkBounds.height}px`;
      indicator.style.transform = `translate(${linkBounds.left - navBounds.left}px, ${linkBounds.top - navBounds.top}px)`;
      indicator.hidden = false;
    }

    positionIndicator();

    // Link sizes and wrapping can change independently of the current route.
    const observer = new ResizeObserver(positionIndicator);
    observer.observe(nav);
    nav.querySelectorAll("a").forEach((link) => observer.observe(link));
    return () => observer.disconnect();
  }, [pathname]);

  return (
    <header className="site-header">
      <Link className="brand" to="/" aria-label="Marshall Course Advisor home">
        <img className="brand-logo" src={marshallLogo} width="240" height="163" alt="" />
        <span className="brand-title">Course Advisor</span>
      </Link>

      <nav className="main-nav" aria-label="Main navigation" ref={navRef}>
        <span className="main-nav-indicator" ref={indicatorRef} aria-hidden="true" hidden />
        {links.map(({ to, label }) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
        <button className="header-sign-out" type="button" onClick={onSignOut}>
          Sign Out
        </button>
      </nav>
    </header>
  );
}

export default Header;

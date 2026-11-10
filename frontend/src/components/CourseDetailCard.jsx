const ICON_PATHS = {
  description: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 12h8 M8 16h8 M8 8h2",
  requirements: "m12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5",
  sections: "M8 2v4 M16 2v4 M3 10h18 M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2 M7 14h2 M15 14h2 M7 18h2 M15 18h2",
};

function CourseDetailCard({ title, icon, action, children }) {
  return (
    <section className="course-detail-card" aria-label={title}>
      <div className="course-detail-card-heading">
        <h2>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={ICON_PATHS[icon]} />
          </svg>
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export default CourseDetailCard;

function CourseDetailCard({ title, className = "", children }) {
  return (
    <section className={`course-detail-card ${className}`} aria-label={title}>
      <div className="course-detail-card-heading">
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

export default CourseDetailCard;

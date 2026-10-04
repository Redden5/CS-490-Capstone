function DashboardCard({ title, children }) {
  return (
    <section className="dashboard-card" aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export default DashboardCard;

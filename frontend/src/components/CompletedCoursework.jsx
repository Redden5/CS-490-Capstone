export default function CompletedCoursework({ rows }) {
  if (!rows.length) return <p className="dashboard-note">No completed coursework saved yet.</p>;

  const scrollable = rows.length > 3;
  return (
    <ul className={`dashboard-coursework${scrollable ? " dashboard-scroll" : ""}`}
      aria-label="Completed course attempts" tabIndex={scrollable ? 0 : undefined}>
      {rows.map((row) => {
        const metadata = [
          row.grade && `Grade ${row.grade}`,
          row.credits && `${row.credits} ${Number(row.credits) === 1 ? "credit" : "credits"}`,
          row.unresolvedEquivalency && "Equivalency needs review",
        ].filter(Boolean).join(" · ");

        return (
          <li key={row.id}>
            <span className="dashboard-course-mark" aria-hidden="true" />
            <div className="dashboard-course-info">
              <strong title={row.code}>{row.code}</strong>
              {metadata && <p className="dashboard-note" title={metadata}>{metadata}</p>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

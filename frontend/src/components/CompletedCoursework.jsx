export default function CompletedCoursework({ rows }) {
  if (!rows.length) return <p className="dashboard-note">No completed coursework saved yet.</p>;

  return (
    <ul className="dashboard-records">
      {rows.map((row) => (
        <li key={row.id}>
          <strong>{row.code}</strong>
          {(row.grade || row.credits || row.unresolvedEquivalency) && (
            <p className="dashboard-note">
              {[
                row.grade && `Grade ${row.grade}`,
                row.credits && `${row.credits} ${Number(row.credits) === 1 ? 'credit' : 'credits'}`,
                row.unresolvedEquivalency && 'Equivalency needs review',
              ].filter(Boolean).join(' · ')}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function CompletedCoursework({ rows }) {
  if (!rows.length) {
    return <p className="mu:m-0 mu:text-sm mu:text-muted">No completed coursework saved yet.</p>;
  }

  const scrollable = rows.length > 3;
  return (
    <ul className={`mu:m-0 mu:list-none mu:divide-y mu:divide-line mu:p-0${scrollable ? " dashboard-scroll mu:max-h-record-list mu:overflow-y-auto mu:student-focus" : ""}`}
      role="list" aria-label="Completed course attempts" tabIndex={scrollable ? 0 : undefined}>
      {rows.map((row) => {
        const details = [
          row.grade && `Grade ${row.grade}`,
          row.credits && `${row.credits} ${Number(row.credits) === 1 ? "credit" : "credits"}`,
        ].filter(Boolean).join(" · ");

        return (
          <li key={row.id} className="mu:flex mu:h-course-row mu:min-w-0 mu:items-center mu:gap-3 mu:py-2 mu:pr-2">
            <span className="mu:size-2 mu:shrink-0 mu:rounded-full mu:bg-herd" aria-hidden="true" />
            <div className="mu:min-w-0">
              <strong className="mu:block mu:truncate mu:text-sm mu:tracking-wide" title={row.code}>{row.code}</strong>
              {details && <p className="mu:m-0 mu:truncate mu:text-xs mu:leading-5 mu:text-muted" title={details}>{details}</p>}
              {row.unresolvedEquivalency && (
                <span className="mu:rounded-sm mu:bg-review-tint mu:px-1.5 mu:py-0.5 mu:text-xs mu:font-medium mu:text-review-ink">
                  Equivalency needs review
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

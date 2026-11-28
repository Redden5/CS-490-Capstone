import { courseworkGpa } from '../lib/courseworkGpa.js';

const creditFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });
const gpaFormat = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function CourseworkGpa({ record }) {
  if (record.loading) return <p className="dashboard-note" role="status">Loading grades and credits…</p>;
  if (record.error) return <p className="dashboard-note">GPA is unavailable while coursework can’t be loaded.</p>;
  const rows = record.input.rows;
  if (!rows.length) return <p className="dashboard-note">Save completed courses with grades and credits in Academic Info to calculate your GPA.</p>;
  const result = courseworkGpa(rows);

  return <>
    <p className="dashboard-gpa-caption">Completed-course GPA estimate</p>
    <div className="dashboard-gpa-value">
      <strong>{result.gpa === null ? '—' : gpaFormat.format(result.gpa)}</strong>
      <span> / 4.00</span>
    </div>
    <p className="dashboard-note">{result.gpa === null
      ? 'Add a supported letter grade and credit amount to calculate GPA.'
      : `${result.includedAttempts} graded ${result.includedAttempts === 1 ? 'attempt' : 'attempts'} · ${creditFormat.format(result.gradedCredits)} GPA credits`}</p>
    {result.excludedGrades > 0 && <p className="dashboard-note">
      {result.excludedGrades} {result.excludedGrades === 1 ? 'entry has a non-GPA grade' : 'entries have non-GPA grades'}.
    </p>}
    {result.missingEvidence > 0 && <p className="dashboard-note">
      {result.missingEvidence} {result.missingEvidence === 1 ? 'entry needs' : 'entries need'} a supported grade or positive credits before inclusion.
    </p>}
    <details className="dashboard-gpa-details">
      <summary>How GPA is calculated</summary>
      <p className="dashboard-note">A=4, B=3, C=2, D=1, F=0. Sum of grade points × credits, divided by GPA credits.</p>
      <p className="dashboard-note">Includes saved transfer entries with supported letter grades. Each saved attempt counts separately. Repeat adjustments and coursework missing from your saved record can change your transcript GPA.</p>
      {result.breakdown.length > 0 && <>
        <dl className="dashboard-gpa-totals">
          <div><dt>Quality points</dt><dd>{creditFormat.format(result.qualityPoints)}</dd></div>
          <div><dt>GPA credits</dt><dd>{creditFormat.format(result.gradedCredits)}</dd></div>
        </dl>
        <ul className="dashboard-gpa-breakdown" aria-label="Credits by grade">
          {result.breakdown.map((group) => <li key={group.grade}>
            <strong>{group.grade}</strong>
            <span>{creditFormat.format(group.credits)} credits × {group.points} = {creditFormat.format(group.qualityPoints)} points</span>
          </li>)}
        </ul>
      </>}
      <a className="dashboard-gpa-policy" href="https://catalog.marshall.edu/undergraduate/academic-information/"
        target="_blank" rel="noopener noreferrer">Marshall grading policy ↗</a>
    </details>
  </>;
}

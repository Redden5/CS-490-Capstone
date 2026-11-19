import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { AuditDisclosure, CourseEvidence, RequirementEvidence } from '../components/PlanningEvidence.jsx';
import { explain, requirementGroups } from '../lib/planningPresentation.js';
import '../styles/DegreeAudit.css';

const panel = 'mu:min-w-0 mu:rounded-panel mu:border mu:border-line mu:bg-paper mu:p-4 mu:shadow-panel mu:sm:p-6';
const heading = 'mu:m-0 mu:text-lg mu:leading-tight mu:font-semibold mu:tracking-tight';
const note = 'mu:m-0 mu:text-sm mu:leading-relaxed mu:text-muted';
const action = 'mu:student-focus mu:inline-flex mu:min-h-11 mu:items-center mu:justify-center mu:gap-2 mu:rounded-lg mu:border mu:px-3 mu:text-sm mu:font-semibold mu:motion-safe:transition-colors';
const link = 'audit-text-link mu:student-focus mu:cursor-pointer mu:border-0 mu:bg-transparent mu:p-0 mu:[font-family:inherit] mu:text-sm mu:font-semibold';
const field = 'mu:student-focus mu:min-h-11 mu:w-full mu:min-w-0 mu:rounded-lg mu:border mu:border-solid mu:border-muted mu:bg-paper mu:px-3 mu:[font-family:inherit] mu:text-sm mu:text-ink';

function AuditNotice({ children, warning = false, role }) {
  return <div role={role}
    className={`mu:space-y-2 mu:rounded-xl mu:border mu:border-line mu:px-4 mu:py-3 mu:text-sm mu:leading-relaxed ${warning ? 'mu:bg-review-tint mu:text-review-ink' : 'mu:bg-mint mu:text-ink'}`}>
    {children}
  </div>;
}

function PrerequisiteResults({ courses }) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [limit, setLimit] = useState(25);
  const query = search.replaceAll(' ', '').toLowerCase();
  const matching = courses.filter((course) => (!status || course.status === status) &&
    course.course_code.replaceAll(' ', '').toLowerCase().includes(query));

  return (
    <section className={panel} aria-labelledby="prerequisite-evidence-title">
      <h2 className={`${heading} mu:student-focus`} id="prerequisite-evidence-title" tabIndex={-1}>Prerequisite evidence</h2>
      <p className={`${note} mu:mt-2`}>These results use your reported coursework. Met conditions do not confirm enrollment eligibility. Unknown results need additional evidence or review.</p>
      <div className="mu:mt-4 mu:grid mu:gap-3 mu:sm:grid-cols-2 mu:xl:grid-cols-1">
        <label className="mu:grid mu:gap-1.5 mu:text-sm mu:font-semibold">
          Find a course
          <input className={`${field} mu:font-normal`} value={search} placeholder="Course code"
            onChange={(e) => { setSearch(e.target.value); setLimit(25); }} />
        </label>
        <label className="mu:grid mu:gap-1.5 mu:text-sm mu:font-semibold">
          Evidence status
          <select className={`${field} mu:font-normal`} value={status}
            onChange={(e) => { setStatus(e.target.value); setLimit(25); }}>
            <option value="">All results</option>
            <option value="MET">Met by reported evidence</option>
            <option value="UNMET">Condition not met</option>
            <option value="UNKNOWN">Uncertain</option>
          </select>
        </label>
      </div>
      <p className={`${note} mu:mt-4 mu:mb-3 mu:font-semibold`} role="status">{matching.length} matching courses</p>
      {!matching.length && (
        <p className={`${note} mu:rounded-lg mu:bg-canvas mu:p-3`}>No results for this selection. An absent course has not been evaluated.</p>
      )}
      <div className="mu:space-y-2">
        {matching.slice(0, limit).map((course) => <CourseEvidence key={course.course_code} course={course} />)}
      </div>
      {matching.length > limit && (
        <button className={`${action} mu:mt-3 mu:cursor-pointer mu:border-solid mu:border-line mu:bg-paper mu:text-herd mu:hover:bg-mint`}
          type="button" onClick={() => setLimit(limit + 25)}>Show more courses</button>
      )}
    </section>
  );
}

function ReportSummary({ result, stale }) {
  const complete = result.summary.candidate_met_rule_occurrences ?? 0;
  const remaining = result.summary.candidate_not_met_rule_occurrences ?? 0;
  const review = result.requirements.length - complete - remaining;
  const reportedCount = result.completed_course_codes_entered?.length ?? 0;
  const progress = [
    { count: complete, label: 'Provisionally satisfied', description: 'Requirements matched by reported evidence', tone: 'mu:bg-mint mu:text-herd' },
    { count: remaining, label: 'Coursework remaining', description: 'Requirements with additional coursework indicated', tone: 'mu:bg-sky-50 mu:text-sky-900' },
    { count: review, label: 'Needs review', description: 'Unresolved rules or insufficient evidence', tone: 'mu:bg-review-tint mu:text-review-ink' },
  ];

  return (
    <section className={panel} aria-labelledby="audit-report-title">
      <header className="mu:flex mu:flex-wrap mu:items-start mu:justify-between mu:gap-3">
        <div className="mu:min-w-0">
          <h2 id="audit-report-title" className="mu:m-0 mu:text-xl mu:font-semibold mu:tracking-tight mu:sm:text-2xl">{result.degree.program_title}</h2>
          <p className={`${note} mu:mt-1`}>Catalog {result.degree.catalog_year} · {reportedCount} distinct reported {reportedCount === 1 ? 'course' : 'courses'}</p>
        </div>
        <span className={`mu:rounded-md mu:px-2.5 mu:py-1 mu:text-xs mu:font-semibold ${stale ? 'mu:bg-review-tint mu:text-review-ink' : 'mu:bg-mint mu:text-herd'}`}>
          {stale ? 'Outdated report' : 'Last successful evaluation'}
        </span>
      </header>
      <dl className="mu:mt-4 mu:mb-0 mu:grid mu:gap-2.5 mu:sm:grid-cols-3">
        {progress.map(({ count, label, description, tone }) => (
          <div key={label} className="mu:grid mu:grid-cols-[auto_minmax(0,1fr)] mu:items-center mu:gap-x-3 mu:gap-y-1 mu:rounded-xl mu:border mu:border-line mu:bg-canvas mu:p-3">
            <dt className="mu:col-start-2 mu:row-start-1 mu:self-end mu:text-sm mu:leading-tight mu:font-semibold">{label}</dt>
            <dd className={`mu:col-start-1 mu:row-span-2 mu:row-start-1 mu:m-0 mu:grid mu:min-w-11 mu:place-items-center mu:rounded-lg mu:px-2 mu:py-2 mu:text-2xl mu:font-semibold mu:tabular-nums ${tone}`}>{count}</dd>
            <dd className="mu:col-start-2 mu:row-start-2 mu:m-0 mu:self-start mu:text-xs mu:leading-snug mu:text-muted">{description}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function AuditEvidence({ result }) {
  const credits = result.distinct_course_credit_evidence;
  const issues = [...(result.course_eligibility.issues ?? []), ...(result.flexible_candidates.retrieval_issues ?? []),
    ...(result.flexible_candidates.completed_course_attribute_evidence?.issues ?? [])];

  return (
    <section className={panel} aria-labelledby="audit-evidence-title">
      <h2 className={heading} id="audit-evidence-title">Evidence available for this audit</h2>
      <dl className="mu:my-3 mu:divide-y mu:divide-line mu:rounded-lg mu:bg-audit-tint mu:px-3 mu:text-sm">
        <div className="mu:flex mu:flex-wrap mu:justify-between mu:gap-x-3 mu:py-2">
          <dt>Prerequisites</dt><dd className="mu:m-0 mu:font-semibold">{explain(result.course_eligibility.evaluation_status)}</dd>
        </div>
        <div className="mu:flex mu:flex-wrap mu:justify-between mu:gap-x-3 mu:py-2">
          <dt>Flexible course options</dt><dd className="mu:m-0 mu:font-semibold">{explain(result.flexible_candidates.generation_status)}</dd>
        </div>
        <div className="mu:flex mu:flex-wrap mu:justify-between mu:gap-x-3 mu:py-2">
          <dt>Known credits for distinct reported courses</dt><dd className="mu:m-0 mu:font-semibold">{credits?.known_credit_subtotal ?? 'Unavailable'}</dd>
        </div>
      </dl>
      <p className={note}>This credit subtotal is an estimate based on reported coursework and catalog evidence. Degree credit still requires verification.</p>
      {credits?.courses_without_known_credits?.length > 0 && <p className={`${note} mu:mt-2`}>Credits still unknown for: {credits.courses_without_known_credits.join(', ')}.</p>}
      {!result.candidate_option_coverage?.unmet_attribute_option_lists_enumerated && (
        <div className="mu:mt-3">
          <AuditNotice warning>Some requirement categories have incomplete course-option lists. A category with missing options may still require coursework.</AuditNotice>
        </div>
      )}
      <div className="mu:mt-3">
        <AuditDisclosure label="Warnings and retrieval details">
          {(result.warnings?.length ?? 0) > 0 && <ul className="mu:m-0 mu:space-y-1 mu:pl-5">{result.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
          <p>Prerequisite retrieval: {explain(result.course_eligibility.retrieval_status)} · Source verification: {explain(result.course_eligibility.source_verification)}</p>
          {issues.length > 0 && <ul className="mu:space-y-1 mu:pl-5">{issues.map((issue, i) => <li key={i}>{explain(issue)}</li>)}</ul>}
        </AuditDisclosure>
      </div>
    </section>
  );
}

export default function DegreeAudit() {
  const { programs, record, reloadAcademics, canSave, saving, saveError, dirty, evaluation, evaluate, stale } = usePlanning();
  const result = evaluation.data;
  useEffect(() => {
    if (record.revision && canSave && !dirty && !result && !evaluation.loading && !evaluation.error && !saveError) {
      void evaluate();
    }
  }, [record.revision, canSave, dirty, result, evaluation.loading, evaluation.error, saveError, evaluate]);
  const eligibility = Object.fromEntries((result?.course_eligibility.courses ?? []).map((course) => [course.course_code, course]));
  const flexible = Object.fromEntries((result?.flexible_candidates.requirements ?? []).map((requirement) => [requirement.requirement_id, requirement]));
  const loading = evaluation.loading || record.loading;

  return (
    <main className="mu:flex-1 mu:bg-canvas mu:font-campus mu:text-ink">
      <div className="mu:mx-auto mu:max-w-[87rem] mu:space-y-4 mu:px-4 mu:pt-6 mu:pb-10 mu:wrap-anywhere mu:sm:px-6 mu:lg:px-10 mu:lg:pt-9">
        <header className="audit-hero mu:flex mu:min-h-40 mu:flex-wrap mu:items-end mu:justify-between mu:gap-4 mu:rounded-banner mu:bg-forest mu:px-6 mu:py-7 mu:text-paper mu:sm:px-8 mu:lg:min-h-48 mu:lg:px-10">
          <div>
            <h1 className="mu:m-0 mu:font-display mu:text-page mu:font-normal mu:tracking-tight">Degree Audit</h1>
            <p className="mu:m-0 mu:mt-2 mu:text-sm mu:leading-relaxed mu:text-leaf mu:sm:text-base">Explore your requirements using your reported coursework.</p>
          </div>
          <Link className={`${action} audit-hero-link mu:border-paper/40 mu:bg-paper/10 mu:hover:bg-paper/20`} to="/academic-info">
            Edit Academic Info <span aria-hidden="true">→</span>
          </Link>
        </header>

        <AuditNotice>This is an unofficial planning audit. Your transcript, graduation eligibility, and permission to enroll require separate confirmation.</AuditNotice>
        {evaluation.loading && <AuditNotice role="status">Evaluating coursework and catalog evidence…</AuditNotice>}
        {record.loading && <AuditNotice role="status">Loading saved coursework…</AuditNotice>}
        {stale && <AuditNotice warning role="status">Outdated results: your academic information has changed. The report below reflects your last successful evaluation.</AuditNotice>}
        {evaluation.error && (
          <AuditNotice warning role="alert">
            <p className="mu:m-0">{evaluation.error.message}</p>
            {result && <p className="mu:m-0">Your last successful report is still shown below.</p>}
            {[404, 409].includes(evaluation.error.status) && <button type="button" className={link} onClick={reloadAcademics}>Reload saved coursework</button>}
          </AuditNotice>
        )}
        {record.error && <AuditNotice warning role="alert">{record.error.message}{' '}
          <button className={link} type="button" onClick={reloadAcademics}>Retry loading coursework</button>
        </AuditNotice>}
        {saveError && <AuditNotice warning role="alert">{saveError.message}{' '}
          <Link className={link} to="/academic-info">Open Academic Info</Link>
        </AuditNotice>}
        {programs.error && <AuditNotice warning role="alert">{programs.error}{' '}
          <button className={link} type="button" onClick={reloadAcademics}>Retry loading programs</button>
        </AuditNotice>}

        <div className="mu:flex mu:flex-wrap mu:gap-2.5">
          <button className={`${action} mu:cursor-pointer mu:border-solid mu:border-herd mu:bg-herd mu:[font-family:inherit] mu:text-paper mu:hover:bg-forest mu:disabled:cursor-not-allowed mu:disabled:opacity-60`}
            type="button" disabled={evaluation.loading || !canSave || saving} onClick={evaluate}>
            {result ? 'Re-evaluate coursework' : 'Evaluate coursework'}
          </button>
          <Link className={`${action} audit-link mu:border-line mu:bg-paper mu:hover:bg-mint`} to="/planner">
            Open Planner <span aria-hidden="true">→</span>
          </Link>
        </div>

        {result ? (
          <>
            <ReportSummary result={result} stale={stale} />
            <div className="mu:grid mu:items-start mu:gap-4 mu:xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
              <section className={panel} aria-labelledby="degree-requirements-title">
                <header className="mu:flex mu:flex-wrap mu:items-center mu:justify-between mu:gap-2">
                  <h2 className={heading} id="degree-requirements-title">Degree requirements</h2>
                  <a className={link} href="#prerequisite-evidence-title">Jump to prerequisites ↓</a>
                </header>
                <p className={`${note} mu:mt-2`}>Expand a requirement to review its options, bundles, and evidence. Credit estimates across requirements can overlap.</p>
                {requirementGroups(result.requirements).map(([name, requirements]) => (
                  <section className="mu:mt-5" key={name}>
                    <h3 className="mu:mt-0 mu:mb-2 mu:text-sm mu:font-semibold mu:text-herd">{name}</h3>
                    <div className="mu:space-y-2">
                      {requirements.map((requirement) => (
                        <RequirementEvidence key={requirement.rule_id} requirement={requirement}
                          flexible={flexible[requirement.rule_id]} eligibility={eligibility} />
                      ))}
                    </div>
                  </section>
                ))}
              </section>
              <div className="mu:min-w-0 mu:space-y-4">
                <AuditEvidence result={result} />
                <PrerequisiteResults key={evaluation.fingerprint} courses={result.course_eligibility.courses} />
              </div>
            </div>
          </>
        ) : loading ? (
          <div className={`${panel} mu:space-y-3 mu:motion-safe:animate-pulse`} aria-hidden="true">
            <div className="mu:h-4 mu:w-1/2 mu:rounded-sm mu:bg-mint" />
            <div className="mu:h-3 mu:w-3/4 mu:rounded-sm mu:bg-canvas" />
            <div className="mu:h-3 mu:w-2/3 mu:rounded-sm mu:bg-canvas" />
          </div>
        ) : (
          <section className={panel}>
            <h2 className={heading}>Your audit starts with Academic Info</h2>
            <p className={`${note} mu:mt-2`}>Select your program and enter successfully completed courses. You can evaluate an empty list if you have not completed any courses yet.</p>
            <p className="mu:mt-3 mu:mb-0"><Link className={link} to="/academic-info">Enter coursework <span aria-hidden="true">→</span></Link></p>
          </section>
        )}
      </div>
    </main>
  );
}

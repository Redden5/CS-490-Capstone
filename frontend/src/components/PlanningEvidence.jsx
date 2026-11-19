import { useState } from 'react';
import { Link } from 'react-router-dom';
import { choiceProgressText, explain, safeSourceUrl } from '../lib/planningPresentation.js';

const badgeTone = {
  met: 'mu:bg-mint mu:text-herd',
  remaining: 'mu:bg-sky-50 mu:text-sky-900',
  review: 'mu:bg-review-tint mu:text-review-ink',
};

const linkStyle = 'audit-text-link mu:student-focus mu:cursor-pointer mu:rounded-sm mu:border-0 mu:bg-transparent mu:p-0 mu:[font-family:inherit] mu:text-sm mu:font-semibold';
const mutedStyle = 'mu:text-sm mu:leading-relaxed mu:text-muted';
const detailHeading = 'mu:mt-0 mu:mb-2 mu:text-sm mu:font-semibold';
const listStyle = 'mu:my-2 mu:space-y-1 mu:pl-5';

export function StatusBadge({ status }) {
  const tone = ['MET', 'CANDIDATE_COMPLETE'].includes(status) ? 'met'
    : ['UNMET', 'CANDIDATE_COURSEWORK_REMAINING'].includes(status) ? 'remaining' : 'review';
  return <span className={`mu:inline-block mu:max-w-full mu:rounded-md mu:px-2 mu:py-1 mu:text-xs mu:leading-snug mu:font-semibold ${badgeTone[tone]}`}>{explain(status)}</span>;
}

export function AuditDisclosure({ label, badge, children }) {
  return (
    <details className="mu:rounded-xl mu:border mu:border-line mu:bg-paper">
      <summary className="audit-summary mu:student-focus mu:flex mu:min-h-12 mu:cursor-pointer mu:items-start mu:gap-3 mu:rounded-xl mu:px-3 mu:py-3 mu:text-sm mu:font-semibold mu:motion-safe:transition-colors mu:hover:bg-audit-tint">
        <span className="audit-chevron mu:mt-1 mu:size-2 mu:shrink-0 mu:motion-safe:transition-transform" aria-hidden="true" />
        <span className="mu:flex mu:min-w-0 mu:flex-1 mu:flex-wrap mu:items-center mu:justify-between mu:gap-x-3 mu:gap-y-2 mu:wrap-anywhere">
          <span>{label}</span>{badge}
        </span>
      </summary>
      <div className="mu:space-y-3 mu:border-t mu:border-line mu:px-3 mu:py-3 mu:text-sm mu:leading-relaxed mu:wrap-anywhere mu:sm:px-4">
        {children}
      </div>
    </details>
  );
}

function Reasons({ items = [] }) {
  return items.length > 0 && <ul className={listStyle}>{items.map((item, index) =>
    <li key={index}>{explain(item)}{item?.table && ` (${item.table})`}</li>)}</ul>;
}

function Source({ url }) {
  const href = safeSourceUrl(url);
  return href && <a className={linkStyle} href={href} target="_blank" rel="noopener noreferrer">Catalog source <span aria-hidden="true">→</span></a>;
}

export function CourseEvidence({ course, nested = false }) {
  const RuleHeading = nested ? 'h4' : 'h3';
  const AlternativeHeading = nested ? 'h5' : 'h4';

  return <AuditDisclosure label={course.course_code} badge={<StatusBadge status={course.status ?? 'UNKNOWN'} />}>
      <Reasons items={course.reasons} />
      <Source url={course.source_url} />
      {Object.entries(course.rule_groups ?? {}).map(([type, group]) => <p key={type}>
        <strong>{type === 'PREREQUISITE' ? 'Prerequisites' : 'Corequisites'}:</strong> {group.status == null ? 'No rule of this type supplied' : explain(group.status)}
      </p>)}
      {(course.rules ?? []).map((rule, index) => <section className="mu:mt-4 mu:border-t mu:border-line mu:pt-4" key={rule.rule_id ?? index}>
        <RuleHeading className={detailHeading}>{rule.rule_type === 'COREQUISITE' ? 'Corequisite rule' : 'Prerequisite rule'} {index + 1}</RuleHeading>
        <StatusBadge status={rule.status} />
        {rule.raw_text && <p>{rule.raw_text}</p>}
        <Reasons items={rule.reasons} />
        <Reasons items={rule.review_flags} />
        {rule.clauses?.length > 0 && <>
          <p className={mutedStyle}>One alternative satisfies this rule. Every condition within that alternative must hold.</p>
          {rule.clauses.map((clause) => <div className="mu:mt-2 mu:rounded-lg mu:border mu:border-line mu:bg-canvas mu:p-3" key={clause.clause_number}>
            <AlternativeHeading className={detailHeading}>Alternative {clause.clause_number} · {explain(clause.status)}</AlternativeHeading>
            <ul className={listStyle}>{clause.conditions.map((condition, i) => <li key={`${condition.course_code}/${i}`}>
              <strong>{condition.course_code}</strong>{condition.minimum_grade && ` · Minimum grade ${condition.minimum_grade}`}
              {condition.concurrent_allowed && ' · Concurrent enrollment allowed by this condition'}
              <p>{explain(condition.status)}</p><Reasons items={condition.reasons} />
            </li>)}</ul>
          </div>)}
        </>}
      </section>)}
      {course.insufficient_grade_evidence?.length > 0 && <div>
        <RuleHeading className={detailHeading}>Grade evidence to review</RuleHeading>
        {course.insufficient_grade_evidence.map((item, index) => <p key={index}>
          {item.course_code}{item.minimum_grade && ` · Minimum ${item.minimum_grade}`}: {(item.reasons ?? []).map(explain).join(' ')}
        </p>)}
      </div>}
      {course.missing_courses?.length > 0 && <p className={mutedStyle}>Courses mentioned across alternative diagnostics: {course.missing_courses.join(', ')}. These are not a list of mandatory additions; an unused alternative may mention courses you do not need.</p>}
      {course.loading && <AuditDisclosure label="Catalog evidence and coverage">
        <p>Rule retrieval: {explain(course.loading.load_status)} · Coverage: {explain(course.loading.coverage_status)}</p>
        <p>Complete rule data: {course.loading.data_complete ? 'Yes' : 'Not established'}</p>
        <Reasons items={course.loading.issues} />
        {(course.loading.rule_provenance ?? []).map((rule, index) => <div key={rule.rule_id ?? index}>
          <p>{rule.raw_text || 'Rule description unavailable.'}</p><Source url={rule.source_url} /><Reasons items={rule.review_flags} />
        </div>)}
      </AuditDisclosure>}
  </AuditDisclosure>;
}

function FlexibleOptions({ flexible, eligibility }) {
  const [showAll, setShowAll] = useState(false);
  const candidates = flexible.candidates ?? [];
  return <section>
    <h4 className={detailHeading}>Catalog course options</h4>
    <p>Coverage: {explain(flexible.generation_status)}. {flexible.coverage?.coverage_complete ? 'Reviewed membership coverage is complete.' : 'Course options may be incomplete.'}</p>
    <Reasons items={flexible.reasons} />
    {!candidates.length && <p>No usable options were returned for this requirement. This does not mean the requirement is complete.</p>}
    <ul role="list" className="mu:my-3 mu:list-none mu:divide-y mu:divide-line mu:p-0">{(showAll ? candidates : candidates.slice(0, 15)).map((candidate) => <li className="mu:flex mu:flex-wrap mu:items-center mu:gap-x-3 mu:gap-y-2 mu:py-3" key={candidate.course_code}>
      <div className="mu:w-full"><strong>{candidate.course_code}</strong>{candidate.course_title && ` — ${candidate.course_title}`}</div>
      <StatusBadge status={eligibility[candidate.course_eligibility_reference ?? candidate.course_code]?.status ?? 'UNKNOWN'} />
      {candidate.min_credit_hours != null && <span>{candidate.min_credit_hours === candidate.max_credit_hours ? candidate.min_credit_hours : `${candidate.min_credit_hours}–${candidate.max_credit_hours}`} catalog credits</span>}
      {candidate.course_id && <Link className={linkStyle} to={`/courses/${candidate.course_id}`}>View course</Link>}
    </li>)}</ul>
    {candidates.length > 15 && <button type="button" className={linkStyle} onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer options' : `Show all ${candidates.length} options`}</button>}
    {flexible.withheld_courses?.length > 0 && <AuditDisclosure label={`${flexible.withheld_courses.length} courses withheld pending evidence`}>
      <ul className={listStyle}>{flexible.withheld_courses.map((course) => <li key={course.course_code}>{course.course_code}: {(course.withheld_reasons ?? []).map(explain).join('; ')}</li>)}</ul>
    </AuditDisclosure>}
    <p className={mutedStyle}>A course may match multiple requirements. These options do not allocate credits or confirm term availability.</p>
  </section>;
}

export function RequirementEvidence({ requirement: r, flexible, eligibility }) {
  return <AuditDisclosure label={r.name} badge={<StatusBadge status={r.planning_disposition} />}>
      <p className={mutedStyle}>{(r.section_path ?? []).join(' / ')}</p>
      <Reasons items={r.remaining_policy_review_flags} />
      {r.required_course_code && <p><strong>Required course:</strong> {r.required_course_code}</p>}
      {r.remaining_required_course_codes?.length > 0 && <p>Not reported completed: {r.remaining_required_course_codes.join(', ')}</p>}
      {r.choice_progress && <>
        <p>Choose {r.choice_progress.required_choices ?? 'the required number of'} options. Options still needed: {r.choice_progress.choices_remaining_candidate ?? 'Needs review'}.</p>
        <ul role="list" className="mu:my-2 mu:list-none mu:divide-y mu:divide-line mu:p-0">{(r.course_options ?? []).map((option, index) => <li className="mu:py-2" key={index}>
          <strong>Option {index + 1}: {option.course_codes.join(option.kind === 'ALL_OF' ? ' + ' : ', ')}</strong>
          {!option.candidate_exclusion_reason && option.kind === 'ALL_OF' && <p>All courses in this option are required to complete the bundle.</p>}
          <p>{choiceProgressText(option)}</p>
          <Reasons items={option.review_flags} />
        </li>)}</ul>
      </>}
      {r.credit_progress && <dl className="mu:grid mu:gap-3 mu:rounded-lg mu:bg-canvas mu:p-3 mu:text-xs mu:sm:grid-cols-3">
        <div><dt>Interpreted credit target</dt><dd className="mu:mt-1 mu:ml-0 mu:text-sm mu:font-semibold">{r.credit_progress.minimum_credits_interpreted ?? 'Unknown'}</dd></div>
        <div><dt>Known candidate credits</dt><dd className="mu:mt-1 mu:ml-0 mu:text-sm mu:font-semibold">{r.credit_progress.candidate_known_credit_subtotal ?? 'Unknown'}</dd></div>
        <div><dt>Additional candidate credits indicated</dt><dd className="mu:mt-1 mu:ml-0 mu:text-sm mu:font-semibold">{r.credit_progress.additional_credits_indicated_candidate ?? 'Needs review'}</dd></div>
      </dl>}
      {r.credit_progress?.candidate_courses_without_known_credits?.length > 0 && <p>Credit evidence missing: {r.credit_progress.candidate_courses_without_known_credits.join(', ')}.</p>}
      {r.attribute_filter && <p>Catalog categories: {(r.attribute_filter.attribute_names ?? []).join(', ') || 'Needs review'}.</p>}
      {flexible ? <FlexibleOptions flexible={flexible} eligibility={eligibility} />
        : r.attribute_filter && <p className={mutedStyle}>A course-option list was not generated for this category. Review its requirements with an advisor.</p>}
      {r.required_course_code && eligibility[r.required_course_code] && <CourseEvidence course={eligibility[r.required_course_code]} nested />}
      <p className={mutedStyle}>Prerequisite evaluation: {explain(r.prerequisite_evaluation_status)}. Offered sections have not been evaluated.</p>
  </AuditDisclosure>;
}

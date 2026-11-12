import { useState } from 'react';
import { Link } from 'react-router-dom';
import { explain, safeSourceUrl } from '../lib/planningPresentation.js';

export function StatusBadge({ status }) {
  const tone = ['MET', 'CANDIDATE_COMPLETE'].includes(status) ? 'met'
    : ['UNMET', 'CANDIDATE_COURSEWORK_REMAINING'].includes(status) ? 'remaining' : 'review';
  return <span className={`planning-badge ${tone}`}>{explain(status)}</span>;
}

function Reasons({ items = [] }) {
  return items.length > 0 && <ul className="planning-reasons">{items.map((item, index) =>
    <li key={index}>{explain(item)}{item?.table && ` (${item.table})`}</li>)}</ul>;
}

function Source({ url }) {
  const href = safeSourceUrl(url);
  return href && <a className="planning-link" href={href} target="_blank" rel="noopener noreferrer">Catalog source ↗</a>;
}

export function CourseEvidence({ course }) {
  return <details className="planning-evidence">
    <summary><strong>{course.course_code}</strong><StatusBadge status={course.status ?? 'UNKNOWN'} /></summary>
    <div className="planning-detail-body">
      <Reasons items={course.reasons} />
      <Source url={course.source_url} />
      {Object.entries(course.rule_groups ?? {}).map(([type, group]) => <p key={type}>
        <strong>{type === 'PREREQUISITE' ? 'Prerequisites' : 'Corequisites'}:</strong> {group.status == null ? 'No rule of this type supplied' : explain(group.status)}
      </p>)}
      {(course.rules ?? []).map((rule, index) => <section className="planning-rule" key={rule.rule_id ?? index}>
        <h4>{rule.rule_type === 'COREQUISITE' ? 'Corequisite rule' : 'Prerequisite rule'} {index + 1}</h4>
        <StatusBadge status={rule.status} />
        {rule.raw_text && <p>{rule.raw_text}</p>}
        <Reasons items={rule.reasons} />
        <Reasons items={rule.review_flags} />
        {rule.clauses?.length > 0 && <>
          <p className="planning-muted">One alternative satisfies this rule. Every condition within that alternative must hold.</p>
          {rule.clauses.map((clause) => <div className="planning-alternative" key={clause.clause_number}>
            <h5>Alternative {clause.clause_number} · {explain(clause.status)}</h5>
            <ul>{clause.conditions.map((condition, i) => <li key={`${condition.course_code}/${i}`}>
              <strong>{condition.course_code}</strong>{condition.minimum_grade && ` · Minimum grade ${condition.minimum_grade}`}
              {condition.concurrent_allowed && ' · Concurrent enrollment allowed by this condition'}
              <p>{explain(condition.status)}</p><Reasons items={condition.reasons} />
            </li>)}</ul>
          </div>)}
        </>}
      </section>)}
      {course.insufficient_grade_evidence?.length > 0 && <div>
        <h4>Grade evidence to review</h4>
        {course.insufficient_grade_evidence.map((item, index) => <p key={index}>
          {item.course_code}{item.minimum_grade && ` · Minimum ${item.minimum_grade}`}: {(item.reasons ?? []).map(explain).join(' ')}
        </p>)}
      </div>}
      {course.missing_courses?.length > 0 && <p className="planning-muted">Courses mentioned across alternative diagnostics: {course.missing_courses.join(', ')}. These are not a list of mandatory additions; an unused alternative may mention courses you do not need.</p>}
      {course.loading && <details className="planning-source-details">
        <summary>Catalog evidence and coverage</summary>
        <p>Rule retrieval: {explain(course.loading.load_status)} · Coverage: {explain(course.loading.coverage_status)}</p>
        <p>Complete rule data: {course.loading.data_complete ? 'Yes' : 'Not established'}</p>
        <Reasons items={course.loading.issues} />
        {(course.loading.rule_provenance ?? []).map((rule, index) => <div key={rule.rule_id ?? index}>
          <p>{rule.raw_text || 'Rule description unavailable.'}</p><Source url={rule.source_url} /><Reasons items={rule.review_flags} />
        </div>)}
      </details>}
    </div>
  </details>;
}

function FlexibleOptions({ flexible, eligibility }) {
  const [showAll, setShowAll] = useState(false);
  const candidates = flexible.candidates ?? [];
  return <section>
    <h4>Catalog course options</h4>
    <p>Coverage: {explain(flexible.generation_status)}. {flexible.coverage?.coverage_complete ? 'Reviewed membership coverage is complete.' : 'Course options may be incomplete.'}</p>
    <Reasons items={flexible.reasons} />
    {!candidates.length && <p>No usable options were returned for this requirement. This does not mean the requirement is complete.</p>}
    <ul className="planning-candidate-list">{(showAll ? candidates : candidates.slice(0, 15)).map((candidate) => <li key={candidate.course_code}>
      <div><strong>{candidate.course_code}</strong>{candidate.course_title && ` — ${candidate.course_title}`}</div>
      <StatusBadge status={eligibility[candidate.course_eligibility_reference ?? candidate.course_code]?.status ?? 'UNKNOWN'} />
      {candidate.min_credit_hours != null && <span>{candidate.min_credit_hours === candidate.max_credit_hours ? candidate.min_credit_hours : `${candidate.min_credit_hours}–${candidate.max_credit_hours}`} catalog credits</span>}
      {candidate.course_id && <Link className="planning-link" to={`/courses/${candidate.course_id}`}>View course</Link>}
    </li>)}</ul>
    {candidates.length > 15 && <button type="button" className="planning-link" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer options' : `Show all ${candidates.length} options`}</button>}
    {flexible.withheld_courses?.length > 0 && <details><summary>{flexible.withheld_courses.length} courses withheld pending evidence</summary>
      <ul>{flexible.withheld_courses.map((course) => <li key={course.course_code}>{course.course_code}: {(course.withheld_reasons ?? []).map(explain).join('; ')}</li>)}</ul>
    </details>}
    <p className="planning-muted">A course may match multiple requirements. These options do not allocate credits or confirm term availability.</p>
  </section>;
}

export function RequirementEvidence({ requirement: r, flexible, eligibility }) {
  return <details className="planning-requirement">
    <summary><span>{r.name}</span><StatusBadge status={r.planning_disposition} /></summary>
    <div className="planning-detail-body">
      <p className="planning-muted">{(r.section_path ?? []).join(' / ')}</p>
      <Reasons items={r.remaining_policy_review_flags} />
      {r.required_course_code && <p><strong>Required course:</strong> {r.required_course_code}</p>}
      {r.remaining_required_course_codes?.length > 0 && <p>Not reported completed: {r.remaining_required_course_codes.join(', ')}</p>}
      {r.choice_progress && <>
        <p>Choose {r.choice_progress.required_choices ?? 'the required number of'} options. Options still needed: {r.choice_progress.choices_remaining_candidate ?? 'Needs review'}.</p>
        <ul className="planning-choice-options">{(r.course_options ?? []).map((option, index) => <li key={index}>
          <strong>Option {index + 1}: {option.course_codes.join(option.kind === 'ALL_OF' ? ' + ' : ', ')}</strong>
          {option.kind === 'ALL_OF' && <p>All courses in this option are required to complete the bundle.</p>}
          <p>{option.fully_entered ? 'All courses reported completed.' : `Still needed for this option: ${option.missing_codes_to_complete_option.join(', ') || 'Needs review'}`}</p>
          <Reasons items={option.review_flags} />
        </li>)}</ul>
      </>}
      {r.credit_progress && <dl className="planning-credit-facts">
        <div><dt>Interpreted credit target</dt><dd>{r.credit_progress.minimum_credits_interpreted ?? 'Unknown'}</dd></div>
        <div><dt>Known candidate credits</dt><dd>{r.credit_progress.candidate_known_credit_subtotal ?? 'Unknown'}</dd></div>
        <div><dt>Additional candidate credits indicated</dt><dd>{r.credit_progress.additional_credits_indicated_candidate ?? 'Needs review'}</dd></div>
      </dl>}
      {r.credit_progress?.candidate_courses_without_known_credits?.length > 0 && <p>Credit evidence missing: {r.credit_progress.candidate_courses_without_known_credits.join(', ')}.</p>}
      {r.attribute_filter && <p>Catalog categories: {(r.attribute_filter.attribute_names ?? []).join(', ') || 'Needs review'}.</p>}
      {flexible ? <FlexibleOptions flexible={flexible} eligibility={eligibility} />
        : r.attribute_filter && <p className="planning-muted">A course-option list was not generated for this category. Review its requirements with an advisor.</p>}
      {r.required_course_code && eligibility[r.required_course_code] && <CourseEvidence course={eligibility[r.required_course_code]} />}
      <p className="planning-muted">Prerequisite evaluation: {explain(r.prerequisite_evaluation_status)}. Offered sections have not been evaluated.</p>
    </div>
  </details>;
}

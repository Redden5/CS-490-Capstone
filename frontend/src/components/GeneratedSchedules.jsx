import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePlanning } from '../context/planningContext.js';
import { saveGeneratorPreferences } from '../lib/studentPlannerApi.js';
import { fetchSectionOptions, generateSchedules, swapScheduleSection, swapScheduleCourse } from '../lib/scheduleApi.js';
import '../styles/GeneratedSchedules.css';

// Matches the weekday convention in backend/scripts/seed_sections.py:
// Monday = 1, Tuesday = 2, ..., Sunday = 7.
const WEEKDAYS = ['Unknown day', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function formatTime(value) {
  if (typeof value !== 'string') return null;
  const match = /^(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(value);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? 'AM' : 'PM'}`;
}

function meetingSummary(section) {
  if (!section.meetings?.length) return 'Meeting times missing / TBA';
  return section.meetings.map((meeting) => {
    const day = WEEKDAYS[Number(meeting.day_of_week)] ?? 'Unknown day';
    const start = formatTime(meeting.start_time);
    const end = formatTime(meeting.end_time);
    return start && end ? `${day} ${start}–${end}` : `${day} · Time TBA`;
  }).join(' · ');
}

function listSummary(items) {
  return items?.length ? items.join(', ') : 'None';
}

export default function GeneratedSchedules({ terms, loadingTerms, initialPreferences, onUseSchedule }) {
  const { evaluation, stale } = usePlanning();
  const handoff = stale ? null : evaluation.data;
  const [termId, setTermId] = useState(String(initialPreferences?.term_id ?? ''));
  const [targetCredits, setTargetCredits] = useState(String(initialPreferences?.target_credits ?? 15));
  const [generation, setGeneration] = useState(null);
  const [optionIndex, setOptionIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [swap, setSwap] = useState(null);
  const request = useRef(null);
  const [addedOptions, setAddedOptions] = useState(() => new Set());
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState('');
  const validCredits = Number(targetCredits) >= 1 && Number(targetCredits) <= 21;
  const termAvailable = terms.some((term) => String(term.term_id) === termId);

  async function saveSettings() {
    setSavingSettings(true);
    setSettingsMessage('');
    try {
      await saveGeneratorPreferences(termId, targetCredits);
      setSettingsMessage('Generator settings saved.');
    } catch (error) {
      setSettingsMessage(error.message);
    } finally {
      setSavingSettings(false);
    }
  }

  const validGeneration = generation?.handoff === handoff &&
    generation.termId === termId ? generation.result : null;
  const chosen = validGeneration?.options?.[optionIndex];

  async function generate() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError('');
    setSwap(null);
    setImportError('');
    void saveSettings();
    try {
      const result = await generateSchedules(handoff, Number(termId), Number(targetCredits), controller.signal);
      if (!controller.signal.aborted) {
        if (!Array.isArray(result?.options) || !Array.isArray(result?.required_courses)) {
          throw new Error('The scheduler returned an unsupported result.');
        }
        setGeneration({ result, handoff, termId });
        setOptionIndex(0);
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  async function startSwap(courseCode) {
    if (!chosen) return;
    setSwap({ kind: 'section', courseCode, loading: true, sections: [], selectedId: '', error: '' });
    try {
      const result = await fetchSectionOptions(courseCode, Number(termId));
      setSwap({ kind: 'section', courseCode, loading: false, sections: result.sections ?? [], selectedId: '', error: '' });
    } catch (err) {
      setSwap({ kind: 'section', courseCode, loading: false, sections: [], selectedId: '', error: err.message });
    }
  }

  function startCourseSwap(courseCode) {
    setSwap({ kind: 'course', courseCode, newCode: '', loading: false,
      sections: [], selectedId: '', error: '' });
  }

  async function selectReplacementCourse(newCode) {
    setSwap((current) => ({ ...current, newCode, sections: [], selectedId: '',
      loading: Boolean(newCode), error: '' }));
    if (!newCode) return;
    try {
      const result = await fetchSectionOptions(newCode, Number(termId));
      setSwap((current) => current?.kind === 'course' && current?.newCode === newCode
        ? { ...current, loading: false, sections: result.sections ?? [] } : current);
    } catch (err) {
      setSwap((current) => current?.kind === 'course' && current?.newCode === newCode
        ? { ...current, loading: false, error: err.message } : current);
    }
  }

  async function confirmSwap() {
    if (!swap?.selectedId || !chosen) return;
    const previousCode = swap.courseCode;
    setSwap((current) => ({ ...current, loading: true, error: '' }));
    try {
      const currentSections = Object.fromEntries(
        Object.entries(chosen.sections).map(([code, section]) => [code, section.section_id])
      );
      const result = swap.kind === 'course'
        ? await swapScheduleCourse(handoff, Number(termId), currentSections,
          previousCode, swap.newCode, Number(swap.selectedId))
        : await swapScheduleSection(handoff, Number(termId), currentSections,
          previousCode, Number(swap.selectedId));
      if (!result.ok) {
        setSwap((current) => ({ ...current, loading: false, error: `Swap rejected: ${listSummary(result.errors)}` }));
        return;
      }
      setGeneration((current) => {
        if (!current || current.handoff !== handoff || current.termId !== termId) return current;
        const options = current.result.options.map((option, index) => index === optionIndex
          ? { ...option, ...result, sections: result.sections } : option);
        return { ...current, result: { ...current.result, options } };
      });
      setSwap(null);
    } catch (err) {
      setSwap((current) => ({ ...current, loading: false, error: err.message }));
    }
  }

  return <section className="schedule-generator" aria-labelledby="schedule-generator-title">
    <h2 id="schedule-generator-title">Generate a schedule from your degree audit</h2>
    <p>Prioritizes required courses and proposes compatible sections. This is an unofficial plan, not registration clearance.</p>
    {!handoff && <p className="schedule-notice">
      {stale ? 'Your degree audit is outdated. Re-evaluate it before generating a schedule.'
        : 'Evaluate your coursework first to identify the remaining degree requirements.'}{' '}
      <Link to="/degree-audit">Open Degree Audit</Link>
    </p>}
    <div className="schedule-form">
      <div className="schedule-fields">
        <label>Academic term
          <select value={termId} disabled={loading || loadingTerms || savingSettings || importing} onChange={(e) => {
            request.current?.abort(); setLoading(false); setTermId(e.target.value); setSwap(null);
          }}>
            <option value="">Choose a term</option>
            {termId && !termAvailable && <option value={termId}>Saved term (currently unavailable)</option>}
            {terms.map((term) => <option key={term.term_id} value={term.term_id}>{term.term_name}</option>)}
          </select>
        </label>
        <label>Target credit hours
          <input type="number" min="1" max="21" step="1" value={targetCredits}
            disabled={savingSettings || importing} onChange={(e) => setTargetCredits(e.target.value)} />
        </label>
      </div>
      <div className="schedule-form-actions">
        <button type="button" className="planner-primary-button" onClick={generate}
          disabled={!handoff || !termAvailable || !validCredits || loading || savingSettings || importing}>
          {loading ? 'Generating…' : 'Generate schedule options'}
        </button>
        <button type="button" className="planner-secondary-button" disabled={!validCredits || savingSettings || importing}
          onClick={saveSettings}>Save generator settings</button>
      </div>
    </div>
    {settingsMessage && <p role="status">{settingsMessage}</p>}
    {error && <p role="alert" className="schedule-error">{error}</p>}
    {validGeneration && <div className="schedule-results">
      <h3>Generated options ({validGeneration.options.length})</h3>
      {validGeneration.search_truncated && <p className="schedule-notice">The search limit was reached; additional schedules may exist.</p>}
      {!validGeneration.options.length && <p>No compatible options were found for the selected term. Review missing courses and offerings below.</p>}
      {!!validGeneration.options.length && <>
        <div className="schedule-option-actions">
          <div className="schedule-option-picker" role="group" aria-label="Generated schedule options">
            {validGeneration.options.map((option, index) => <button type="button" key={index}
              className={index === optionIndex ? 'schedule-option active' : 'schedule-option'}
              aria-pressed={index === optionIndex}
              disabled={importing} onClick={() => { setOptionIndex(index); setSwap(null); setImportError(''); }}>Option {index + 1} · {option.total_credits} credits</button>)}
          </div>

          <button type="button" className="planner-primary-button"
            disabled={loading || importing || Boolean(swap) || addedOptions.has(chosen)}
            onClick={async () => {
              setImporting(true);
              setImportError('');
              try {
                await onUseSchedule({
                  termId, option: chosen, creditHours: validGeneration.course_credit_hours,
                  handoffWarnings: validGeneration.handoff_warnings ?? [],
                });
                setAddedOptions((current) => new Set([...current, chosen]));
              } catch (error) {
                setImportError(error.message);
              } finally {
                setImporting(false);
              }
            }}>
            {importing ? 'Saving schedule…' : addedOptions.has(chosen) ? 'Added to semester plan' : 'Use this schedule'}
          </button>
        </div>
        {importError && <p role="alert" className="schedule-error">{importError}</p>}
        {addedOptions.has(chosen) && <p role="status">This schedule was saved as a semester plan.</p>}

        <p className="schedule-result-status" data-tentative={chosen.status === 'TENTATIVE'}><strong>Status:</strong> {chosen.status === 'TENTATIVE' ? 'Tentative: unresolved checks' : 'Known checks passed (not registration clearance)'}</p>
        <ul className="schedule-selected-sections" role="list">
          {Object.entries(chosen.sections).map(([code, section]) => <li key={code}>
            <div className="schedule-course-detail">
              <strong>{code}</strong>
              <p>Section {section.section_number} · CRN {section.crn}</p>
              <p>{meetingSummary(section)}</p>
              <p className="schedule-subtle">Section status: {section.section_status ?? 'UNKNOWN'}</p>
            </div>
            <div className="schedule-row-actions">
              <button type="button" className="planner-secondary-button" disabled={importing} onClick={() => startSwap(code)}>Change section</button>
              {!validGeneration.required_courses.includes(code) &&
                validGeneration.choice_groups?.some((group) => group.course_codes.includes(code) &&
                  group.course_codes.some((option) => option !== code && !chosen.sections[option])) &&
                <button type="button" className="planner-secondary-button" disabled={importing} onClick={() => startCourseSwap(code)}>Change course</button>}
            </div>
          </li>)}
        </ul>
        <p><strong>Required courses not scheduled this term:</strong> {listSummary(chosen.unscheduled_required)}</p>
        {!!chosen.warnings?.length && <details><summary>{chosen.warnings.length} unresolved checks</summary>
          <ul>{chosen.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </details>}
      </>}
      {!!Object.keys(validGeneration.excluded_courses ?? {}).length && <details>
        <summary>Courses without usable sections or credit information</summary>
        <ul>{Object.entries(validGeneration.excluded_courses).map(([code, reason]) => <li key={code}>{code}: {reason}</li>)}</ul>
      </details>}
      {!!validGeneration.handoff_warnings?.length && <details><summary>Degree requirements requiring further review</summary>
        <ul>{validGeneration.handoff_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      </details>}
    </div>}
    {chosen && swap && <div className="schedule-swap" role="group" aria-label={`Change ${swap.kind} for ${swap.courseCode}`}>
      <h3>{swap.kind === 'course' ? 'Change flexible course' : 'Change section'}: {swap.courseCode}</h3>
      {swap.kind === 'course' && <label>Approved alternatives from the same choice requirement
        <select value={swap.newCode} disabled={swap.loading} onChange={(e) => selectReplacementCourse(e.target.value)}>
          <option value="">Choose a replacement course</option>
          {[...new Set((validGeneration.choice_groups ?? [])
            .filter((group) => group.course_codes.includes(swap.courseCode))
            .flatMap((group) => group.course_codes))]
            .filter((code) => code !== swap.courseCode && !chosen.sections[code] &&
              !validGeneration.required_courses.includes(code))
            .map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
      </label>}
      {swap.loading ? <p role="status">Checking sections…</p> : <>
        <label>Available recorded sections
          <select value={swap.selectedId} onChange={(e) => setSwap((current) => ({ ...current, selectedId: e.target.value }))}>
            <option value="">Choose a section</option>
            {swap.sections.map((section) => <option key={section.section_id} value={section.section_id}>
              {section.section_number} · CRN {section.crn} · {meetingSummary(section)}
            </option>)}
          </select>
        </label>
        {swap.error && <p role="alert" className="schedule-error">{swap.error}</p>}
        <div className="schedule-swap-actions">
          <button type="button" className="planner-primary-button" disabled={!swap.selectedId} onClick={confirmSwap}>Apply change</button>
          <button type="button" className="planner-secondary-button" onClick={() => setSwap(null)}>Cancel</button>
        </div>
      </>}
    </div>}
    <p className="schedule-subtle">Required courses cannot be replaced. Course changes are limited to reviewed single-course choice groups; Core II, bundled requirements, and other substitutions still require degree-policy approval.</p>
  </section>;
}

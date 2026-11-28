import { useState } from 'react';
import PageHeading from '../components/PageHeading.jsx';
import { Link, useNavigate } from 'react-router-dom';
import CompletedCourseSearch from '../components/CompletedCourseSearch.jsx';
import { usePlanning } from '../context/planningContext.js';
import '../styles/Planning.css';
import '../styles/AcademicInfo.css';

const PAGE_SIZE = 5;

export default function AcademicInfo() {
  const { input, updateInput, programs, record, reloadAcademics, canSave, saving, save, saveError, dirty, evaluation, evaluate, stale } = usePlanning();
  const navigate = useNavigate();
  const [manualEntryId, setManualEntryId] = useState(null);
  const [requestedPage, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(input.rows.length / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount);
  const end = input.rows.length - (page - 1) * PAGE_SIZE;
  const visibleRows = input.rows.slice(Math.max(0, end - PAGE_SIZE), end).reverse();
  const selected = programs.data.findIndex((p) => p.program_key === input.program?.program_key && p.catalog_year === input.program?.catalog_year);

  function addCourse(code = '') {
    const row = { id: crypto.randomUUID(), code, grade: '', credits: '', unresolvedEquivalency: false };
    setPage(1);
    setManualEntryId(code ? null : row.id);
    updateInput((current) => ({ ...current, rows: [...current.rows, row] }));
  }
  function editRow(id, field, value) {
    updateInput((current) => ({ ...current, rows: current.rows.map((row) => row.id === id ? { ...row, [field]: value } : row) }));
  }
  async function submit(event) {
    event.preventDefault();
    if (event.nativeEvent.submitter?.value === 'save') {
      await save();
      return;
    }
    if (await evaluate()) navigate('/degree-audit');
  }

  return <main className="planning-page app-page academic-page">
    <PageHeading title="Academic Info" description="Enter completed coursework to explore your degree requirements.">
      <Link className="planning-button secondary" to="/degree-audit">View Degree Audit →</Link>
    </PageHeading>
    {record.loading && <p role="status">Loading saved coursework…</p>}
    {record.error && <p role="alert">{record.error.message}{' '}
      <button type="button" className="planning-link" onClick={reloadAcademics}>Retry loading coursework</button>
    </p>}
    {stale && <p className="planning-notice warning" role="status">Your coursework has changed. Evaluate it again to update your Degree Audit.</p>}

    <section className="academic-program" aria-labelledby="program-title">
      <div>
        <h2 id="program-title">Program and catalog year</h2>
        <p>Choose the catalog for your degree. It determines which requirements appear in your audit.</p>
      </div>
      <div>
          {programs.loading ? <p role="status">Loading supported programs…</p> : programs.error ? (
            <div role="alert"><p>{programs.error}</p><button type="button" className="planning-button secondary" onClick={reloadAcademics}>Retry loading programs</button></div>
          ) : programs.data.length ? (
            <label className="planning-field" htmlFor="planning-program">Supported program
              <select id="planning-program" disabled={record.loading || Boolean(record.error) || saving} value={selected < 0 ? '' : selected} onChange={(e) =>
                updateInput((current) => ({ ...current, program: programs.data[Number(e.target.value)] }))}>
                {selected < 0 && <option value="" disabled>Select a program</option>}
                {programs.data.map((program, index) => <option key={`${program.program_key}/${program.catalog_year}`} value={index}>
                  {program.program_title} · {program.catalog_year}
                </option>)}
              </select>
            </label>
          ) : <p>No programs are available. <button className="planning-link" type="button" onClick={reloadAcademics}>Refresh programs</button></p>}
      </div>
    </section>

    <div className="academic-workspace">
      {!record.loading && !record.error && !saving && <CompletedCourseSearch onAdd={addCourse} />}
      <form className="planning-card planning-coursework" onSubmit={submit}>
        <div className="planning-section-heading"><h2>Completed coursework</h2><span>{input.rows.length} {input.rows.length === 1 ? 'entry' : 'entries'}</span></div>
        {!record.loading && !record.error && <p className={dirty ? "academic-save-state pending" : "academic-save-state"} role="status">{dirty ? 'You have unsaved academic changes.'
      : record.revision ? 'Your academic information is saved.' : 'Save your academic information to use it the next time you sign in.'}</p>}
        <p className="planning-muted">Include only courses you passed. Leave unknown grades or credits blank. Grades help check prerequisites; they do not verify degree credit.</p>
        <details className="academic-entry-guide">
          <summary>Repeated courses and transfer grades</summary>
          <p>Record each successful attempt separately. Conflicting grades, plus/minus grades, and transfer marks may need review.</p>
        </details>
        <button type="button" className="planning-button secondary" disabled={record.loading || Boolean(record.error) || saving} onClick={() => addCourse()}>Add course manually</button>
        {!input.rows.length && <p className="planning-empty">No coursework entered. Search for a course or add one manually. You can also evaluate with no completed courses.</p>}
        {saveError && <div className="planning-notice warning" role="alert">
          <p>{saveError.message}</p>
          {saveError.status === 409 && <button type="button" className="planning-link" onClick={reloadAcademics}>Discard edits and reload saved coursework</button>}
        </div>}
        {evaluation.error && <div className="planning-notice warning" role="alert">
          <p>{evaluation.error.message}</p>
          {evaluation.error.fields?.length > 0 && <p>Check: {evaluation.error.fields.map((field) => field.replaceAll('_', ' ')).join(', ')}.</p>}
          {[404, 409].includes(evaluation.error.status) && <button type="button" className="planning-link" onClick={reloadAcademics}>Reload saved coursework</button>}
        </div>}
        <div className="planning-evaluate">
          <button className="planning-button" disabled={!canSave || evaluation.loading || saving} type="submit" value="evaluate">
            {evaluation.loading ? 'Evaluating coursework…' : 'Evaluate my coursework'}
          </button>
          <button className="planning-button secondary" type="submit" value="save"
            disabled={!canSave || saving || evaluation.loading || (!dirty && Boolean(record.revision))}>
            {saving ? 'Saving coursework…' : 'Save coursework'}
          </button>
          <p className="planning-muted" role="status">{evaluation.loading ? 'Checking degree requirements and catalog evidence. You can keep editing; edits cancel this evaluation.' : 'This produces an unofficial planning audit based on your reported coursework.'}</p>
        </div>
        {pageCount > 1 && <label className="academic-page-picker">
          Coursework page
          <select value={page} onChange={event => {
            setManualEntryId(null);
            setPage(Number(event.target.value));
          }}>
            {Array.from({ length: pageCount }, (_, index) => {
              const last = input.rows.length - index * PAGE_SIZE;
              return <option key={index} value={index + 1}>
                Page {index + 1} of {pageCount} · Courses {last}–{Math.max(1, last - PAGE_SIZE + 1)}
              </option>;
            })}
          </select>
        </label>}
        <ol className="planning-coursework-list">
          {visibleRows.map((row, index) => <li key={row.id}>
            <fieldset disabled={record.loading || Boolean(record.error) || saving}>
              <legend>Course {end - index}{row.code && ` · ${row.code}`}</legend>
              <div className="planning-course-fields">
                <label className="planning-field">Course code
                  <input autoFocus={row.id === manualEntryId} value={row.code} required maxLength={40} placeholder="CS 110" onChange={(e) => editRow(row.id, 'code', e.target.value)} />
                </label>
                <label className="planning-field">Grade (optional)
                  <input value={row.grade} maxLength={20} placeholder="A, B, C, D…" list="planning-grades" onChange={(e) => editRow(row.id, 'grade', e.target.value)} />
                </label>
                <label className="planning-field">Credits (optional)
                  <input value={row.credits} type="number" min="0.01" step="any" placeholder="Unknown" onChange={(e) => editRow(row.id, 'credits', e.target.value)} />
                </label>
              </div>
              <label className="planning-checkbox"><input type="checkbox" checked={row.unresolvedEquivalency}
                onChange={(e) => editRow(row.id, 'unresolvedEquivalency', e.target.checked)} /> Transfer or equivalency still needs review</label>
              <button type="button" className="planning-link" aria-label={`Remove coursework entry ${end - index}`}
                onClick={() => updateInput((current) => ({ ...current, rows: current.rows.filter((item) => item.id !== row.id) }))}>Remove entry</button>
            </fieldset>
          </li>)}
        </ol>
        <datalist id="planning-grades">{['A', 'B', 'C', 'D'].map((grade) => <option key={grade} value={grade} />)}</datalist>
      </form>
    </div>
  </main>;
}

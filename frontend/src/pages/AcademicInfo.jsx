import { Link, useNavigate } from 'react-router-dom';
import CompletedCourseSearch from '../components/CompletedCourseSearch.jsx';
import { usePlanning } from '../context/planningContext.js';
import '../styles/Planning.css';

export default function AcademicInfo() {
  const { input, updateInput, programs, refreshPrograms, evaluation, evaluate, stale } = usePlanning();
  const navigate = useNavigate();
  const selected = programs.data.findIndex((p) => p.program_key === input.program?.program_key && p.catalog_year === input.program?.catalog_year);

  function addCourse(code = '') {
    const row = { id: crypto.randomUUID(), code, grade: '', credits: '', unresolvedEquivalency: false };
    updateInput((current) => ({ ...current, rows: [...current.rows, row] }));
  }
  function editRow(id, field, value) {
    updateInput((current) => ({ ...current, rows: current.rows.map((row) => row.id === id ? { ...row, [field]: value } : row) }));
  }
  async function submit(event) {
    event.preventDefault();
    if (await evaluate()) navigate('/degree-audit');
  }

  return <main className="planning-page">
    <header className="planning-heading">
      <div><p className="planning-eyebrow">Your academic record</p><h1>Academic Info</h1>
        <p>Enter successfully completed coursework to explore your degree requirements.</p></div>
      <Link className="planning-link" to="/degree-audit">View Degree Audit →</Link>
    </header>
    <p className="planning-notice">Your coursework and results stay in this session. Refreshing the page or signing out clears them.</p>
    {stale && <p className="planning-notice warning" role="status">Your coursework has changed. Evaluate it again to update your Degree Audit.</p>}

    <div className="planning-input-layout">
      <div>
        <section className="planning-card" aria-labelledby="program-title">
          <h2 id="program-title">Program and catalog year</h2>
          {programs.loading ? <p role="status">Loading supported programs…</p> : programs.error ? (
            <div role="alert"><p>{programs.error}</p><button type="button" className="planning-button secondary" onClick={refreshPrograms}>Retry loading programs</button></div>
          ) : programs.data.length ? (
            <label className="planning-field" htmlFor="planning-program">Supported program
              <select id="planning-program" value={selected < 0 ? '' : selected} onChange={(e) =>
                updateInput((current) => ({ ...current, program: programs.data[Number(e.target.value)] }))}>
                {selected < 0 && <option value="" disabled>Select a program</option>}
                {programs.data.map((program, index) => <option key={`${program.program_key}/${program.catalog_year}`} value={index}>
                  {program.program_title} · {program.catalog_year}
                </option>)}
              </select>
            </label>
          ) : <p>No programs are available. <button className="planning-link" type="button" onClick={refreshPrograms}>Refresh programs</button></p>}
          <p className="planning-muted">Choose the catalog year that applies to your program. Only supported combinations are listed.</p>
        </section>
        <CompletedCourseSearch onAdd={addCourse} />
      </div>

      <form className="planning-card planning-coursework" onSubmit={submit}>
        <div className="planning-section-heading"><h2>Completed coursework</h2><span>{input.rows.length} {input.rows.length === 1 ? 'entry' : 'entries'}</span></div>
        <p className="planning-muted">Include only courses you passed. Leave unknown grades or credits blank. Grades help check prerequisites; they do not verify degree credit.</p>
        <p className="planning-muted">Record repeated successful attempts separately. Conflicting grades, plus/minus grades, and transfer marks may need review.</p>
        {!input.rows.length && <p className="planning-empty">No coursework entered. Search for a course or add one manually. You can also evaluate with no completed courses.</p>}
        <ol className="planning-coursework-list">
          {input.rows.map((row, index) => <li key={row.id}>
            <fieldset>
              <legend>Course {index + 1}{row.code && ` · ${row.code}`}</legend>
              <div className="planning-course-fields">
                <label className="planning-field">Course code
                  <input value={row.code} required maxLength={40} placeholder="CS 110" onChange={(e) => editRow(row.id, 'code', e.target.value)} />
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
              <button type="button" className="planning-link" aria-label={`Remove coursework entry ${index + 1}`}
                onClick={() => updateInput((current) => ({ ...current, rows: current.rows.filter((item) => item.id !== row.id) }))}>Remove entry</button>
            </fieldset>
          </li>)}
        </ol>
        <datalist id="planning-grades">{['A', 'B', 'C', 'D'].map((grade) => <option key={grade} value={grade} />)}</datalist>
        <button type="button" className="planning-button secondary" onClick={() => addCourse()}>Add course manually</button>
        {evaluation.error && <div className="planning-notice warning" role="alert">
          <p>{evaluation.error.message}</p>
          {evaluation.error.fields?.length > 0 && <p>Check: {evaluation.error.fields.map((field) => field.replaceAll('_', ' ')).join(', ')}.</p>}
          {evaluation.error.status === 404 && <button type="button" className="planning-link" onClick={refreshPrograms}>Refresh programs</button>}
        </div>}
        <div className="planning-evaluate">
          <button className="planning-button" disabled={evaluation.loading || programs.loading || Boolean(programs.error) || !input.program} type="submit">
            {evaluation.loading ? 'Evaluating coursework…' : 'Evaluate my coursework'}
          </button>
          <p className="planning-muted" role="status">{evaluation.loading ? 'Checking degree requirements and catalog evidence. You can keep editing; edits cancel this evaluation.' : 'This produces an unofficial planning audit based on your reported coursework.'}</p>
        </div>
      </form>
    </div>
  </main>;
}

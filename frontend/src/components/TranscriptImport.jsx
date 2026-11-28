import { useEffect, useRef, useState } from 'react';
import { importTranscript } from '../lib/planningApi.js';

const MAX_PDF_BYTES = 5 * 1024 * 1024;

export default function TranscriptImport({ onImport }) {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const pending = useRef(null);

  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event) {
    event.preventDefault();
    if (!file || pending.current) return;
    setError('');
    setStatus('');
    if (!/\.pdf$/i.test(file.name) || file.size > MAX_PDF_BYTES) {
      setError('Choose a PDF file no larger than 5 MB.');
      return;
    }

    const form = event.currentTarget;
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const { courses, skipped_courses } = await importTranscript(file, controller.signal);
      if (controller.signal.aborted) return;
      if (courses.length) onImport(courses);
      const imported = courses.length
        ? `Added ${courses.length} coursework ${courses.length === 1 ? 'entry' : 'entries'}. Review the list against your transcript before evaluating.`
        : 'No recognized rows had a passing grade eligible for import.';
      const skipped = skipped_courses
        ? ` Skipped ${skipped_courses} recognized ${skipped_courses === 1 ? 'row' : 'rows'} without an eligible passing grade.`
        : '';
      setStatus(imported + skipped);
      form.reset();
      setFile(null);
    } catch (problem) {
      if (!controller.signal.aborted) setError(problem.message);
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return <form className="planning-card planning-transcript" aria-labelledby="transcript-import-title" onSubmit={submit}>
    <h2 id="transcript-import-title">Import transcript</h2>
    <p id="transcript-import-help" className="planning-muted">Upload a transcript PDF with selectable text (up to 5 MB). Recognized passing courses are added to your existing coursework.</p>
    <label className="planning-field">Transcript PDF
      <input type="file" accept=".pdf,application/pdf" aria-describedby="transcript-import-help" disabled={loading}
        onChange={(event) => {
          setFile(event.target.files[0] ?? null);
          setError('');
          setStatus('');
        }} />
    </label>
    <button type="submit" className="planning-button secondary" disabled={!file || loading}>
      {loading ? 'Importing transcript…' : 'Import coursework'}
    </button>
    {error && <p className="planning-notice warning" role="alert">{error}</p>}
    <div role="status">{loading ? 'Reading transcript…' : status && <p className="planning-notice">{status}</p>}</div>
  </form>;
}

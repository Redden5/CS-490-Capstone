import { useEffect, useRef, useState } from 'react';
import { PlanningContext } from './planningContext.js';
import { fetchPrograms } from '../lib/planningApi.js';
import { evaluateAcademics, loadAcademics, saveAcademics } from '../lib/studentAcademicsApi.js';

const emptyInput = { program: null, rows: [] };
const emptyEvaluation = { loading: false, data: null, error: null, fingerprint: null };

function toInput(record, programs) {
  if (!record) return { ...emptyInput, program: programs[0] ?? null };
  return {
    program: programs.find((p) => p.program_key === record.program_key && p.catalog_year === record.catalog_year)
      ?? { program_key: record.program_key, catalog_year: record.catalog_year },
    rows: record.attempts.map((attempt) => ({
      id: attempt.id, code: attempt.course_code, grade: attempt.grade ?? '',
      credits: String(attempt.reported_credits ?? ''), unresolvedEquivalency: !attempt.equivalency_verified,
    })),
  };
}

export default function PlanningProvider({ children }) {
  const [input, setInput] = useState(emptyInput);
  const [programs, setPrograms] = useState({ loading: true, data: [], error: null });
  const [record, setRecord] = useState({ loading: true, revision: null, input: emptyInput, error: null });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [evaluation, setEvaluation] = useState(emptyEvaluation);
  const pending = useRef(null);
  const pendingSave = useRef(null);
  const dirty = JSON.stringify(input) !== JSON.stringify(record.input);
  const supported = programs.data.some((p) => p.program_key === input.program?.program_key
    && p.catalog_year === input.program?.catalog_year);
  const canSave = !record.loading && !record.error && !programs.loading && !programs.error && supported;

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([fetchPrograms(controller.signal), loadAcademics(controller.signal)]).then(([available, saved]) => {
      if (controller.signal.aborted) return;
      const data = available.status === 'fulfilled' ? available.value : [];
      setPrograms({ loading: false, data, error: available.reason?.message ?? null });
      if (saved.status === 'rejected') {
        setRecord({ loading: false, revision: null, input: emptyInput, error: saved.reason });
        return;
      }
      const loaded = toInput(saved.value, data);
      setInput(loaded);
      setRecord({ loading: false, revision: saved.value?.revision ?? null, input: loaded, error: null });
    });
    return () => controller.abort();
  }, [loadAttempt]);

  useEffect(() => () => { pending.current?.abort(); pendingSave.current?.abort(); }, []);

  function updateInput(transform) {
    if (record.loading || record.error || pendingSave.current) return;
    pending.current?.abort();
    setInput(transform);
    setEvaluation((current) => ({ ...current, loading: false, error: null }));
    setSaveError((error) => error?.status === 409 ? error : null);
  }

  function reloadAcademics() {
    if (pendingSave.current) return;
    pending.current?.abort();
    setPrograms((current) => ({ ...current, loading: true, error: null }));
    setRecord((current) => ({ ...current, loading: true, error: null }));
    setSaveError(null);
    setEvaluation(emptyEvaluation);
    setLoadAttempt((value) => value + 1);
  }

  async function save() {
    if (!canSave || pendingSave.current) return null;
    if (record.revision && !dirty) return record;
    const controller = new AbortController();
    pendingSave.current = controller;
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await saveAcademics(input, record.revision, controller.signal);
      if (controller.signal.aborted) return null;
      const normalized = toInput(saved, programs.data);
      const next = { loading: false, revision: saved.revision, input: normalized, error: null };
      setInput(normalized);
      setRecord(next);
      return next;
    } catch (error) {
      if (!controller.signal.aborted) setSaveError(error);
      return null;
    } finally {
      pendingSave.current = null;
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  async function evaluate() {
    if (!canSave || pendingSave.current || evaluation.loading) return false;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setEvaluation((current) => ({ ...current, loading: true, error: null }));
    try {
      const saved = await save();
      if (!saved || controller.signal.aborted) return false;
      const data = await evaluateAcademics(saved.revision, saved.input.program.catalog_year, controller.signal);
      if (controller.signal.aborted) return false;
      setEvaluation({ loading: false, data, error: null, fingerprint: JSON.stringify(saved.input) });
      return true;
    } catch (error) {
      if (!controller.signal.aborted) setEvaluation((current) => ({ ...current, error }));
      return false;
    } finally {
      if (!controller.signal.aborted) setEvaluation((current) => ({ ...current, loading: false }));
    }
  }

  return <PlanningContext.Provider value={{
    input, updateInput, programs, record, reloadAcademics, canSave, saving, save, saveError, dirty, evaluation, evaluate,
    stale: Boolean(evaluation.data && (record.loading || evaluation.error?.status === 409
      || saveError?.status === 409 || evaluation.fingerprint !== JSON.stringify(input))),
  }}>
    {children}
  </PlanningContext.Provider>;
}

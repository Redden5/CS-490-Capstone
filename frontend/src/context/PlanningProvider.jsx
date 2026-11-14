import { useEffect, useRef, useState } from 'react';
import { PlanningContext } from './planningContext.js';
import { buildPlanningRequest, evaluatePlanning, fetchPrograms } from '../lib/planningApi.js';

export default function PlanningProvider({ children }) {
  const [input, setInput] = useState({ program: null, rows: [] });
  const [programs, setPrograms] = useState({ loading: true, data: [], error: null });
  const [programAttempt, setProgramAttempt] = useState(0);
  const [evaluation, setEvaluation] = useState({ loading: false, data: null, error: null, fingerprint: null });
  const pending = useRef(null);
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchPrograms(controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setPrograms({ loading: false, data, error: null });
      setInput((current) => ({ ...current, program: data.find((p) =>
        p.program_key === current.program?.program_key && p.catalog_year === current.program?.catalog_year) ?? data[0] ?? null }));
    }).catch((error) => {
      if (!controller.signal.aborted) setPrograms({ loading: false, data: [], error: error.message });
    });
    return () => controller.abort();
  }, [programAttempt]);

  useEffect(() => () => { generation.current++; pending.current?.abort(); }, []);

  function updateInput(transform) {
    generation.current++;
    pending.current?.abort();
    setInput(transform);
    setEvaluation((current) => ({ ...current, loading: false, error: null }));
  }

  function refreshPrograms() {
    updateInput((current) => current);
    setPrograms((current) => ({ ...current, loading: true, error: null }));
    setProgramAttempt((value) => value + 1);
  }

  async function evaluate() {
    pending.current?.abort();
    const requestId = ++generation.current;
    const controller = new AbortController();
    pending.current = controller;
    try {
      if (programs.loading || programs.error) throw new Error('Load the available programs before evaluating.');
      const body = buildPlanningRequest(input);
      const fingerprint = JSON.stringify(input);
      setEvaluation((current) => ({ ...current, loading: true, error: null }));
      const data = await evaluatePlanning(body, controller.signal);
      if (controller.signal.aborted || generation.current !== requestId) return false;
      setEvaluation({ loading: false, data, error: null, fingerprint });
      return true;
    } catch (error) {
      if (!controller.signal.aborted && generation.current === requestId) {
        setEvaluation((current) => ({ ...current, loading: false, error }));
      }
      return false;
    }
  }

  return <PlanningContext.Provider value={{ input, updateInput, programs, refreshPrograms, evaluation, evaluate,
    stale: Boolean(evaluation.data && evaluation.fingerprint !== JSON.stringify(input)) }}>
    {children}
  </PlanningContext.Provider>;
}

import { useEffect, useRef, useState } from 'react';

export function useCourseSearch() {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState({ term: '', attempt: 0 });
  const [page, setPage] = useState(0);
  const timer = useRef(null);
  const committed = useRef('');
  useEffect(() => {
    timer.current = setTimeout(() => {
      if (committed.current === input.trim()) return;
      committed.current = input.trim();
      setSearch((current) => ({ term: input.trim(), attempt: current.attempt + 1 }));
      setPage(0);
    }, 300);
    return () => clearTimeout(timer.current);
  }, [input]);
  function submit(event) {
    event?.preventDefault();
    clearTimeout(timer.current);
    committed.current = input.trim();
    setPage(0);
    setSearch((current) => ({ term: input.trim(), attempt: current.attempt + 1 }));
  }
  function reset() {
    clearTimeout(timer.current);
    committed.current = '';
    setInput(''); setPage(0);
    setSearch((current) => ({ term: '', attempt: current.attempt + 1 }));
  }
  return { input, setInput, search, page, setPage, submit, reset };
}

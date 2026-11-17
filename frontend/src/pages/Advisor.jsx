import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { askAdvisor } from "../lib/advisorApi.js";
import "../styles/Advisor.css";

const COURSE_EXAMPLES = [
  {
    code: "CS 215", title: "Adv Data Struct and Algorithms",
    reason: "Explore a core computer science course.",
    source: "Marshall course catalog",
  },
  {
    code: "CS 410", title: "Database Engineering",
    reason: "Compare against your completed coursework.",
    source: "Marshall course catalog",
  },
];

const DRAFT_EXAMPLES = [
  { code: "CS 215", title: "Adv Data Struct and Algorithms", credits: 3 },
  { code: "MTH 229", title: "Calculus / Analytic Geometry I", credits: 5 },
  { code: "ENG 201", title: "Advanced Composition", credits: 3 },
];

const INTRO = { id: 'welcome', answer: 'Ask about a Marshall course, its prerequisites, available schedule records, or degree requirements. Try “What are the prerequisites for CS 215?”' };

const ICON_PATHS = {
  calendar: "M8 2v4 M16 2v4 M3 10h18 M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2",
  chat: "M21 11a8 8 0 0 1-8 8H8l-5 3v-7a8 8 0 0 1 0-8 8 8 0 0 1 7-4h3a8 8 0 0 1 8 8Z",
  send: "m22 2-7 20-4-9-9-4z M22 2 11 13",
  external: "M14 3h7v7 M21 3 10 14 M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5",
  user: "M20 21v-2a7 7 0 0 0-14 0v2 M17 7a5 5 0 1 1-10 0 5 5 0 0 1 10 0",
  assistant: "M12 2v3 M7 5h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3 M1 11v5 M23 11v5 M8 11h.01 M16 11h.01 M8 16h8",
};

function AdvisorIcon({ name }) {
  return <svg className="advisor-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={ICON_PATHS[name]} />
  </svg>;
}

function Advisor() {
  const [chatExpanded, setChatExpanded] = useState(true);
  const [messages, setMessages] = useState([INTRO]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(null);
  const courseCodes = useRef([]);
  const chatLog = useRef(null);
  const messageInput = useRef(null);

  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (chatExpanded && chatLog.current) chatLog.current.scrollTop = chatLog.current.scrollHeight;
  }, [messages, chatExpanded, loading]);

  async function submit(event) {
    event.preventDefault();
    const text = question.trim();
    if (!text || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const id = crypto.randomUUID();
    setQuestion('');
    setError('');
    setLoading(true);
    setMessages((current) => [...current.slice(-38), { id, question: text }]);
    try {
      const result = await askAdvisor(text, courseCodes.current, controller.signal);
      if (controller.signal.aborted) return;
      courseCodes.current = result.course_codes;
      setMessages((current) => [...current, { id: `${id}-reply`, ...result }]);
    } catch (problem) {
      if (!controller.signal.aborted) {
        setMessages((current) => current.filter((message) => message.id !== id));
        setQuestion(text);
        setError(problem.message);
      }
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  return (
    <main className="advisor-page">
      <header className="advisor-heading">
        <div>
          <h1>AI Advisor</h1>
          <p>Plan your next semester and get answers grounded in your course catalog.</p>
        </div>
      </header>

      <section className="advisor-context" aria-label="Sample academic context">
        <div><span>Program</span><strong>B.S. in Computer Science</strong></div>
        <div><span>Catalog</span><strong>2026–2027</strong></div>
        <div>
          <span>About this workspace</span>
          <p>Ask about course details, prerequisites, schedule records, and degree requirements.</p>
        </div>
      </section>

      <div className="advisor-workspace">
        <section className="advisor-panel advisor-recommendations" aria-labelledby="advisor-courses-title">
          <div className="advisor-panel-heading">
            <h2 id="advisor-courses-title">Courses to consider</h2>
            <label className="advisor-catalog-control">Catalog
              <select defaultValue="2026-2027" aria-label="Recommendation catalog year">
                <option value="2026-2027">2026–2027</option>
              </select>
            </label>
          </div>
          <p className="advisor-panel-intro">Example courses from the Computer Science catalog.</p>
          <div className="advisor-course-list">
            {COURSE_EXAMPLES.map((course) => (
              <article className="advisor-course-card" key={course.code}>
                <div className="advisor-course-copy">
                  <h3>{course.code} · {course.title}</h3>
                  <p><strong>Reason:</strong> {course.reason}</p>
                  <p className="advisor-course-source"><span>Source:</span> {course.source}</p>
                </div>
                <div className="advisor-course-actions">
                  <button type="button" className="advisor-text-button" disabled={loading} onClick={() => {
                    setChatExpanded(true);
                    setError('');
                    setQuestion(`What are the prerequisites for ${course.code}?`);
                    requestAnimationFrame(() => messageInput.current?.focus());
                  }}>
                    View requirements <AdvisorIcon name="external" />
                  </button>
                  <button type="button" className="advisor-outline-button" disabled>
                    <span aria-hidden="true">＋</span> Add to draft
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="advisor-panel advisor-draft" aria-labelledby="advisor-draft-title">
          <h2 id="advisor-draft-title">Next semester draft</h2>
          <p className="advisor-draft-summary">Sample draft · 11 credits</p>
          <ul className="advisor-draft-courses">
            {DRAFT_EXAMPLES.map((course) => (
              <li key={course.code}>
                <strong>{course.code} · {course.title}</strong><span>{course.credits} credits</span>
              </li>
            ))}
          </ul>
          <Link className="advisor-primary-button advisor-open-planner" to="/planner">
            <AdvisorIcon name="calendar" /> Open Planner
          </Link>
        </section>
      </div>

      <section className="advisor-panel advisor-chat" aria-labelledby="advisor-chat-title">
        <div className="advisor-chat-heading">
          <div className="advisor-chat-heading-copy">
            <AdvisorIcon name="chat" />
            <div>
              <h2 id="advisor-chat-title">Ask about your plan</h2>
              <p>Get help with course details, prerequisites, scheduling, and degree requirements.</p>
            </div>
          </div>
          <button type="button" className="advisor-chat-toggle" aria-expanded={chatExpanded}
            aria-controls="advisor-conversation" onClick={() => setChatExpanded(!chatExpanded)}>
            <svg className="advisor-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" aria-hidden="true"><path d={chatExpanded ? "m5 15 7-7 7 7" : "m5 9 7 7 7-7"} /></svg>
            {chatExpanded ? "Collapse conversation" : "Expand conversation"}
          </button>
        </div>
        <div id="advisor-conversation" hidden={!chatExpanded}>
          <div className="advisor-messages" role="log" aria-label="Advisor conversation" ref={chatLog}>
            {messages.map((message) => message.question ? (
              <div key={message.id} className="advisor-message advisor-user-message">
                <p>{message.question}</p>
                <span className="advisor-avatar"><span className="sr-only">Student</span><AdvisorIcon name="user" /></span>
              </div>
            ) : (
              <div key={message.id} className="advisor-message advisor-assistant-message">
                <span className="advisor-avatar"><span className="sr-only">Advisor</span><AdvisorIcon name="assistant" /></span>
                <div className="advisor-message-bubble">
                  <p className="advisor-answer">{message.answer}</p>
                  {message.sources?.length > 0 && <ul className="advisor-chat-source" aria-label="Sources">
                    {message.sources.map((source) => (
                      <li key={source.title}>{source.url
                        ? <a className="advisor-text-button" href={source.url} target="_blank" rel="noreferrer">
                          {source.title} <AdvisorIcon name="external" />
                        </a>
                        : source.title}</li>
                    ))}
                  </ul>}
                  {message.action === 'planner' && <Link className="advisor-text-button" to="/planner">Open Planner</Link>}
                  {message.action === 'audit' && <Link className="advisor-text-button" to="/academic-info">Open Academic Info</Link>}
                </div>
              </div>
            ))}
          </div>
          {error && <p className="advisor-feedback" role="alert">{error}</p>}
          {loading && <p className="advisor-feedback" role="status">Looking up academic records…</p>}
          <form className="advisor-composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="advisor-message">Ask a follow-up</label>
            <input id="advisor-message" ref={messageInput} placeholder="Ask about a course or your plan…" value={question}
              maxLength={500} onChange={(event) => setQuestion(event.target.value)} readOnly={loading} />
            <button className="advisor-primary-button" type="submit" disabled={loading || !question.trim()}><AdvisorIcon name="send" /> Send</button>
          </form>
        </div>
      </section>
    </main>
  );
}

export default Advisor;

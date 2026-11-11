import { useState } from "react";
import { Link } from "react-router-dom";
import "../styles/Advisor.css";

const COURSE_EXAMPLES = [
  {
    code: "CS 215", title: "Data Structures",
    reason: "Explore a core computer science course.",
    source: "CS 210 and math requirements apply.",
  },
  {
    code: "CS 410", title: "Database Engineering",
    reason: "Compare against your completed coursework.",
    source: "CS 210 with a minimum grade of D.",
  },
];

const DRAFT_EXAMPLES = [
  { code: "CS 215", title: "Data Structures", credits: 3 },
  { code: "MTH 229", title: "Calculus", credits: 3 },
  { code: "ENG 201", title: "Writing", credits: 3 },
];

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

  return (
    <main className="advisor-page">
      <header className="advisor-heading">
        <div>
          <h1>AI Advisor</h1>
          <p>Plan your next semester and get answers grounded in your course catalog.</p>
        </div>
        <span className="advisor-preview-badge">Design preview · Sample data</span>
      </header>

      <section className="advisor-context" aria-label="Sample academic context">
        <div><span>Program</span><strong>B.S. in Computer Science</strong></div>
        <div><span>Catalog</span><strong>2026–2027</strong></div>
        <div>
          <span>About this workspace</span>
          <p>Get course recommendations, build a next-semester draft, and ask questions about courses, prerequisites, and policies.</p>
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
          <p className="advisor-panel-intro">Recommended courses to explore for your next semester, based on your program and catalog.</p>
          <div className="advisor-course-list">
            {COURSE_EXAMPLES.map((course) => (
              <article className="advisor-course-card" key={course.code}>
                <div className="advisor-course-copy">
                  <h3>{course.code} · {course.title}</h3>
                  <p><strong>Reason:</strong> {course.reason}</p>
                  <p className="advisor-course-source"><span>Source:</span> {course.source}</p>
                </div>
                <div className="advisor-course-actions">
                  <button type="button" className="advisor-text-button" disabled>
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
          <p className="advisor-draft-summary">Sample draft · 9 credits</p>
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
              <p>Get help with semester planning, prerequisites, course options, and university policies.</p>
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
          <div className="advisor-messages" aria-label="Sample conversation">
            <div className="advisor-message advisor-user-message">
              <p>How should I balance this semester?</p>
              <span className="advisor-avatar"><span className="sr-only">Student</span><AdvisorIcon name="user" /></span>
            </div>
            <div className="advisor-message advisor-assistant-message">
              <span className="advisor-avatar"><span className="sr-only">Advisor</span><AdvisorIcon name="assistant" /></span>
              <div className="advisor-message-bubble">
                <p>Compare course workloads and confirm each requirement before adding courses to your plan.</p>
                <p className="advisor-chat-source">Source: <button type="button" className="advisor-text-button" disabled>
                  View catalog requirements <AdvisorIcon name="external" />
                </button></p>
              </div>
            </div>
          </div>
          <div className="advisor-composer">
            <label className="sr-only" htmlFor="advisor-message">Ask a follow-up</label>
            <input id="advisor-message" placeholder="Ask a follow-up…" disabled aria-describedby="advisor-preview-note" />
            <button className="advisor-primary-button" type="button" disabled><AdvisorIcon name="send" /> Send</button>
          </div>
        </div>
        <p className="advisor-preview-note" id="advisor-preview-note">Recommendations, draft courses, and conversation are examples. Chat and recommendation actions are not available yet.</p>
      </section>
    </main>
  );
}

export default Advisor;


import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";

import { supabase } from "./lib/supabaseClient.js";

import Header from "./components/Header.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import AcademicInfo from "./pages/AcademicInfo.jsx";
import Courses from "./pages/Courses.jsx";
import DegreeAudit from "./pages/DegreeAudit.jsx";
import Planner from "./pages/Planner.jsx";
import Advisor from "./pages/Advisor.jsx";
import Login from "./pages/Login.jsx";

function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    const { data: { subscription } } =
      supabase.auth.onAuthStateChange((_event, session) => {
        if (active) {
          setSession(session);
          setLoading(false);
        }
      });

    supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return;

      if (error) {
        console.error("Session error:", error);
      }

      setSession(data?.session ?? null);
      setLoading(false);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  async function handleSignOut() {
    const { error } = await supabase.auth.signOut();

    if (error) {
      console.error("Error signing out:", error);
      alert("Unable to sign out. Please try again.");
    }
  }

  if (loading) {
    return <p>Loading application...</p>;
  }

  return (
    <BrowserRouter>
      {!session ? (
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="*"
            element={<Navigate to="/login" replace />}
          />
        </Routes>
      ) : (
        <>
          <Header onSignOut={handleSignOut}/>

          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/academic-info" element={<AcademicInfo />} />
            <Route path="/courses" element={<Courses />} />
            <Route path="/degree-audit" element={<DegreeAudit />} />
            <Route path="/planner" element={<Planner />} />
            <Route path="/advisor" element={<Advisor />} />
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </>
      )}
    </BrowserRouter>
  );
}

export default App;

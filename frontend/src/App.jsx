import { BrowserRouter, Routes, Route } from "react-router-dom";
import Header from "./components/Header.jsx";

import Dashboard from "./pages/Dashboard.jsx";
import AcademicInfo from "./pages/AcademicInfo.jsx";
import Courses from "./pages/Courses.jsx";
import DegreeAudit from "./pages/DegreeAudit.jsx";
import Planner from "./pages/Planner.jsx";
import Advisor from "./pages/Advisor.jsx";

function App() {
  return (
    <BrowserRouter>
      <Header />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/academic-info" element={<AcademicInfo />} />
        <Route path="/courses" element={<Courses />} />
        <Route path="/degree-audit" element={<DegreeAudit />} />
        <Route path="/planner" element={<Planner />} />
        <Route path="/advisor" element={<Advisor />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;

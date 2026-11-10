import { useState } from "react";
import CourseDetailCard from "./CourseDetailCard.jsx";

// Keep catalog years separate: the same course can have different requirements.
function CourseRequirements({ rules }) {
  const catalogs = [...new Map(rules.map((rule) => [
    String(rule.catalog_year_id ?? "undated"),
    {
      id: String(rule.catalog_year_id ?? "undated"),
      label: rule.catalogyear?.catalog_label || (rule.catalog_year_id == null
        ? "Catalog year not recorded" : `Catalog ${rule.catalog_year_id}`),
      start: rule.catalogyear?.start_year ?? -1,
      end: rule.catalogyear?.end_year ?? -1,
    },
  ])).values()].sort((a, b) => b.start - a.start || b.end - a.end || a.id.localeCompare(b.id));
  const [selectedCatalog, setSelectedCatalog] = useState(null);
  const catalogId = selectedCatalog ?? catalogs[0]?.id;
  const selectedRules = rules.filter((rule) => String(rule.catalog_year_id ?? "undated") === catalogId);

  return (
    <CourseDetailCard title="Prerequisites and corequisites" icon="requirements"
      action={catalogs.length > 0 && (
        <div className="course-catalog-control">
          <label htmlFor="requirements-catalog">Catalog year</label>
          <select id="requirements-catalog" value={catalogId}
            aria-describedby="requirements-catalog-help"
            onChange={(event) => setSelectedCatalog(event.target.value)}>
            {catalogs.map((catalog) => <option key={catalog.id} value={catalog.id}>{catalog.label}</option>)}
          </select>
        </div>
      )}>
      {catalogs.length > 0 && <p id="requirements-catalog-help" className="course-catalog-help">
        Choose the catalog year that applies to your program.
      </p>}
      <div className="course-requirements-text">
        {selectedRules.length ? selectedRules.map((rule) => (
          <p key={rule.rule_id} className="course-description">{rule.raw_text?.trim()
            || "Requirements are recorded, but their description is not available."}</p>
        )) : <p>No requirements listed.</p>}
      </div>
      <p className="course-requirements-note">Listed requirements do not confirm your eligibility to enroll.</p>
    </CourseDetailCard>
  );
}

export default CourseRequirements;

import { useState } from "react";

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
    <>
      {catalogs.length > 0 && (
        <div>
          <label htmlFor="requirements-catalog">Requirements catalog year</label>{" "}
          <select id="requirements-catalog" value={catalogId}
            onChange={(event) => setSelectedCatalog(event.target.value)}>
            {catalogs.map((catalog) => <option key={catalog.id} value={catalog.id}>{catalog.label}</option>)}
          </select>
          <p>Choose the catalog year that applies to your program.</p>
        </div>
      )}
      {selectedRules.length ? selectedRules.map((rule) => (
        <p key={rule.rule_id} className="course-description">{rule.raw_text?.trim()
          || "Requirements are recorded, but their description is not available."}</p>
      )) : <p>No requirements listed.</p>}
      <p>Listed requirements do not confirm your eligibility to enroll.</p>
    </>
  );
}

export default CourseRequirements;

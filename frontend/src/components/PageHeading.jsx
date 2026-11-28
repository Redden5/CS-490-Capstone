export default function PageHeading({ eyebrow, title, description, children }) {
  return <header className="app-page-heading">
    <div>
      {eyebrow && <p className="app-eyebrow">{eyebrow}</p>}
      <h1>{title}</h1>
      {description && <p className="app-page-description">{description}</p>}
    </div>
    {children && <div className="app-heading-actions">{children}</div>}
  </header>;
}

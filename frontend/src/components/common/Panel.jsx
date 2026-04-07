export default function Panel({ title, actions, children, className = '' }) {
  return (
    <div className={`panel ${className}`}>
      {title && (
        <div className="panel-header">
          <h3>{title}</h3>
          {actions && <div>{actions}</div>}
        </div>
      )}
      <div className="panel-body">{children}</div>
    </div>
  );
}

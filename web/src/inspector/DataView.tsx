// Renders whatever JSON an inspector returns: arrays of records as tables, objects as
// key-value lists, everything else as text. Store and queue inspectors use it.
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function scalar(v: unknown): string {
  if (v === null || v === undefined) return '·'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

export function RecordTable({ rows, caption }: { rows: Record<string, unknown>[]; caption?: string }) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 8)
  if (rows.length === 0) return <p className="text-xs text-muted-foreground">No records.</p>
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-left text-[11px]">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-muted/60">
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col" className="px-2 py-1 font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t align-top">
              {cols.map((c) => (
                <td key={c} className="max-w-48 truncate px-2 py-1 font-mono" title={scalar(r[c])}>
                  {scalar(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function KeyValues({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value)
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">Empty.</p>
  return (
    <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-1 text-[11px]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="font-mono break-all">{isRecord(v) || Array.isArray(v) ? JSON.stringify(v) : scalar(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function DataView({ value, name }: { value: unknown; name?: string }) {
  if (Array.isArray(value)) {
    if (value.every(isRecord)) return <RecordTable rows={value} caption={name} />
    return <p className="font-mono text-[11px]">{value.map(scalar).join(', ') || 'None.'}</p>
  }
  if (isRecord(value)) {
    const values = Object.values(value)
    if (values.length > 0 && values.every(isRecord)) {
      return <RecordTable rows={Object.entries(value).map(([k, v]) => ({ name: k, ...(v as Record<string, unknown>) }))} caption={name} />
    }
    return <KeyValues value={value} />
  }
  return <p className="font-mono text-[11px]">{scalar(value)}</p>
}

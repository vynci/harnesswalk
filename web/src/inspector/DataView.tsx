import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

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
    <div className="rounded-lg border">
      <Table className="text-xs">
        {caption && <TableCaption className="sr-only">{caption}</TableCaption>}
        <TableHeader className="bg-muted/60">
          <TableRow>
            {cols.map((c) => (
              <TableHead key={c} scope="col" className="h-7 px-2">
                {c}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i} className="align-top">
              {cols.map((c) => (
                <TableCell key={c} className="max-w-48 truncate px-2 py-1 font-mono" title={scalar(r[c])}>
                  {scalar(r[c])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function KeyValues({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value)
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">Empty.</p>
  return (
    <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-1 text-xs">
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
    return <p className="font-mono text-xs">{value.map(scalar).join(', ') || 'None.'}</p>
  }
  if (isRecord(value)) {
    const values = Object.values(value)
    if (values.length > 0 && values.every(isRecord)) {
      return <RecordTable rows={Object.entries(value).map(([k, v]) => ({ name: k, ...(v as Record<string, unknown>) }))} caption={name} />
    }
    return <KeyValues value={value} />
  }
  return <p className="font-mono text-xs">{scalar(value)}</p>
}

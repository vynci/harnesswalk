import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { stringify } from 'yaml'
import type { NodeInfo } from '@/api/types'
import { CodeView } from './CodeView'
import { DataView } from './DataView'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-muted-foreground">{title}</h4>
      {children}
    </section>
  )
}

export function DetailsTab({ info }: { info: NodeInfo }) {
  const ins = info.inspector
  if (ins.type === 'config') {
    const content = typeof ins.content === 'string' ? ins.content : ins.effective !== undefined ? stringify(ins.effective) : null
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <p className="text-xs text-muted-foreground">
          {typeof ins.path === 'string' ? <code className="font-mono">{ins.path}</code> : 'No config file'}
          {typeof ins.version === 'string' ? ` · ${ins.version}` : ''}
          {' · read-only in P2'}
        </p>
        {content ? <CodeView code={content} language="yaml" label="Config" className="flex-1" /> : <p className="text-xs">Nothing configured.</p>}
      </div>
    )
  }
  if (ins.type === 'prompt') {
    const items = Array.isArray(ins.items) ? (ins.items as { name: string; version?: string; description?: string; content?: string }[]) : []
    return (
      <div className="flex flex-col gap-4">
        {items.length === 0 && <p className="text-xs text-muted-foreground">No prompt or skills loaded.</p>}
        {items.map((item) => (
          <article key={item.name} className="rounded-md border p-3">
            <header className="mb-2 flex items-baseline gap-2">
              <h4 className="font-mono text-xs font-semibold">{item.name}</h4>
              {item.version && <span className="text-[11px] text-muted-foreground">{item.version}</span>}
            </header>
            <div className="hw-md text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content ?? ''}</ReactMarkdown>
            </div>
          </article>
        ))}
      </div>
    )
  }
  // store, queue, model and anything newer: one section per field.
  const fields = Object.entries(ins).filter(([k]) => k !== 'type')
  return (
    <div className="flex flex-col gap-4">
      {fields.length === 0 && <p className="text-xs text-muted-foreground">Nothing to show yet.</p>}
      {fields.map(([k, v]) => (
        <Section key={k} title={k.replace(/_/g, ' ')}>
          <DataView value={v} name={k} />
        </Section>
      ))}
    </div>
  )
}

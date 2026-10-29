import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { stringify } from 'yaml'
import type { NodeInfo } from '@/api/types'
import { Code } from '@/components/code'
import { Prose } from '@/components/prose'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { DataView, KeyValues } from './DataView'
import { sourceExtras } from './labels'
import { isAuditPayload, isDocList, isProposalList, isRecord, isScopedRecords } from './shapes'
import { AuditView, CitationList, ContextBudget, DocList, PendingView, RecordList, WritesView } from './StoreViews'

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
        {content ? (
          <Code code={content} language="yaml" lineNumbers size="sm" label="Config" contentClassName="text-[12px]" data-testid="config-view" />
        ) : (
          <p className="text-xs">Nothing configured.</p>
        )}
      </div>
    )
  }
  if (ins.type === 'prompt') {
    const items = Array.isArray(ins.items) ? (ins.items as { name: string; version?: string; description?: string; content?: string }[]) : []
    return (
      <div className="flex flex-col gap-4">
        {items.length === 0 && <p className="text-xs text-muted-foreground">No prompt or skills loaded.</p>}
        {items.map((item) => (
          <Card key={item.name} className="gap-2 py-3 shadow-none">
            <CardHeader className="flex items-baseline gap-2 px-3">
              <CardTitle className="font-mono text-xs">{item.name}</CardTitle>
              {item.version && <span className="text-[11px] text-muted-foreground">{item.version}</span>}
            </CardHeader>
            <CardContent className="px-3">
              <Prose>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content ?? ''}</ReactMarkdown>
              </Prose>
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }
  // An audit export: the hash chain with a Verify action.
  if (isAuditPayload(ins)) return <AuditView ins={ins} />
  // store, queue, model, a code node's own state and anything newer: one section per field,
  // each drawn by the shape of its value.
  const keys = ins.type === 'source' ? sourceExtras(ins) : Object.keys(ins).filter((k) => k !== 'type')
  const fields = keys.map((k) => [k, ins[k]] as const)
  return (
    <div className="flex flex-col gap-4">
      {fields.length === 0 && <p className="text-xs text-muted-foreground">Nothing to show yet.</p>}
      {fields.map(([k, v]) => (
        <Section key={k} title={k.replace(/_/g, ' ')}>
          <FieldView name={k} value={v} />
        </Section>
      ))}
    </div>
  )
}

function FieldView({ name, value }: { name: string; value: unknown }) {
  if (name === 'last_writes' && isProposalList(value)) return <WritesView writes={value} />
  if (name === 'pending' && isProposalList(value)) return <PendingView pending={value} />
  if (isDocList(value)) return <DocList docs={value as Record<string, unknown>[]} />
  if (Array.isArray(value) && value.length > 0 && isScopedRecords(value)) return <RecordList rows={value} />
  if (isRecord(value) && isRecord(value.slices)) {
    const cites = Array.isArray(value.citations) ? value.citations : []
    return (
      <div className="flex flex-col gap-3">
        <ContextBudget summary={{ ...value, citations: cites.length }} />
        <CitationList citations={cites as never} />
      </div>
    )
  }
  if (isRecord(value) && Object.values(value).every((v) => !isRecord(v) && !Array.isArray(v))) return <KeyValues value={value} />
  return <DataView value={value} name={name} />
}

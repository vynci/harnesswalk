// Edit one working-set artifact (a prompt, a skill, a YAML config): view, Edit as a mono
// Textarea with Save and Cancel and a preview of the change, then the API's diff in a Code
// Block. Blocked, with the reason, in public mode, without `features.artifact_edit`, or while a
// turn runs (the API would answer 409 turn_running).
import { useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { GitCompareArrows, Pencil } from 'lucide-react'
import type { ArtifactSaved } from '@/api/types'
import { Code } from '@/components/code'
import { DiffView } from '@/components/diff-view'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceDescription, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { diffLines, diffStats, parseUnifiedDiff } from '@/lib/diff'
import { artifactLabel, editBlockReason, type EditableArtifact } from '@/state/ops'
import { selectTurnRunning, useApp } from '@/state/store'
import { yamlProblem } from './opsView'

export type ArtifactLanguage = 'markdown' | 'yaml'

export function ArtifactEditor({
  artifact,
  stamp,
  language,
  body,
  renderBody,
}: {
  artifact: EditableArtifact
  /** The content stamp the harness reports (`returns@b0e44e5b`): the same text again after a rollback. */
  stamp?: string
  language: ArtifactLanguage
  /** The body as the inspector already has it; fetched from the API when absent. */
  body?: string
  renderBody?: (body: string) => ReactNode
}) {
  const api = useApp((s) => s.api)
  const capabilities = useApp((s) => s.capabilities)
  const running = useApp(selectTurnRunning)
  const saveArtifact = useApp((s) => s.saveArtifact)
  const blocked = editBlockReason(capabilities, running)
  const label = artifactLabel(artifact.kind, artifact.name)
  const [loaded, setLoaded] = useState<{ version: number; body: string } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ base: string; text: string; note: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<ArtifactSaved | null>(null)
  const reasonId = useId()
  const editorId = useId()

  // The body to show, when the inspector did not carry it (a YAML artifact behind a code node).
  useEffect(() => {
    if (body !== undefined || !api) return
    let cancelled = false
    api.artifact(artifact.kind, artifact.name).then(
      (a) => !cancelled && setLoaded({ version: a.version, body: a.body }),
      (err: unknown) => !cancelled && setLoadError(err instanceof Error ? err.message : String(err)),
    )
    return () => {
      cancelled = true
    }
  }, [api, artifact.kind, artifact.name, artifact.version, body])

  const shown = body ?? loaded?.body
  const preview = useMemo(() => (draft ? diffLines(draft.base, draft.text) : []), [draft])
  const stats = diffStats(preview)
  const problem = draft && language === 'yaml' ? yamlProblem(draft.text) : null

  const startEdit = async () => {
    if (blocked || !api) return
    setSaved(null)
    try {
      // Edit the latest version, whatever the inspector showed.
      const latest = await api.artifact(artifact.kind, artifact.name)
      setDraft({ base: latest.body, text: latest.body, note: '' })
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err))
    }
  }

  const save = async () => {
    if (!draft || blocked || problem) return
    setSaving(true)
    const out = await saveArtifact(artifact.kind, artifact.name, draft.text, draft.note.trim() || undefined)
    setSaving(false)
    if (out) {
      setDraft(null)
      setSaved(out)
      setLoaded({ version: out.version, body: out.body })
    }
  }

  return (
    <section className="flex flex-col gap-2" data-testid="artifact-editor" data-kind={artifact.kind} data-name={artifact.name} aria-label={`${label} artifact`}>
      <div className="flex items-center gap-2">
        <code className="min-w-0 truncate font-mono text-xs font-medium">{artifact.name}</code>
        <span className="text-xs text-muted-foreground tabular-nums" data-testid="artifact-version">
          v{Math.max(saved?.version ?? 0, artifact.version)}
          {artifact.updated_by ? ` · ${artifact.updated_by}` : ''}
          {typeof artifact.served_version === 'number' && artifact.served_version !== artifact.version ? ` · turns use v${artifact.served_version}` : ''}
        </span>
        {stamp && (
          <Hint label="The content stamp turns record (procedural prompt_version): a rollback brings the old stamp back">
            <code className="min-w-0 truncate font-mono text-xs text-muted-foreground" tabIndex={0} data-testid="artifact-stamp">
              {stamp}
            </code>
          </Hint>
        )}
        {!draft && (
          <Hint label={blocked ?? `Edit ${label}: Save makes a new working-set version`}>
            <Button
              size="xs"
              variant="outline"
              className="ml-auto"
              aria-disabled={blocked ? true : undefined}
              aria-describedby={blocked ? reasonId : undefined}
              onClick={() => void startEdit()}
              aria-label={`Edit ${label}`}
              data-testid="artifact-edit"
            >
              <Pencil aria-hidden /> Edit
            </Button>
          </Hint>
        )}
        {blocked && (
          <span id={reasonId} className="sr-only">
            {blocked}
          </span>
        )}
      </div>
      {loadError && <p className="text-xs text-destructive">Could not load {label}: {loadError}</p>}
      {draft ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={editorId} className="sr-only">
            {label}, {language}
          </label>
          <Textarea
            id={editorId}
            value={draft.text}
            onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            spellCheck={false}
            className="max-h-[60vh] min-h-64 font-mono text-xs leading-relaxed md:text-xs"
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${editorId}-problem` : undefined}
            data-testid="artifact-textarea"
          />
          {problem && (
            <p id={`${editorId}-problem`} role="alert" className="text-xs text-destructive">
              Not valid YAML: {problem}
            </p>
          )}
          <Input
            value={draft.note}
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
            placeholder="What changed and why (optional)"
            aria-label="Change note"
            data-testid="artifact-note"
            className="h-8 text-xs"
          />
          {stats.added + stats.removed > 0 && (
            <Trace>
              <TraceCollapsible>
                <TraceCollapsibleTrigger>
                  <TraceLine>
                    <TraceIcon>
                      <GitCompareArrows />
                    </TraceIcon>
                    <TraceTitle>Review the change</TraceTitle>
                    <TraceDescription>
                      +{stats.added} −{stats.removed}
                    </TraceDescription>
                  </TraceLine>
                </TraceCollapsibleTrigger>
                <TraceCollapsibleContent>
                  <DiffView lines={preview.filter((l, i, all) => l.type !== 'same' || near(all, i))} label={`Preview of the change to ${label}`} />
                </TraceCollapsibleContent>
              </TraceCollapsible>
            </Trace>
          )}
          <div className="flex items-center gap-2">
            {blocked && <span className="text-xs text-muted-foreground">{blocked}</span>}
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setDraft(null)} disabled={saving}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => void save()} disabled={saving || !!blocked || !!problem || stats.added + stats.removed === 0} data-testid="artifact-save">
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      ) : shown !== undefined ? (
        renderBody ? (
          renderBody(shown)
        ) : (
          <Code code={shown} language={language} lineNumbers size="sm" label={label} contentClassName="max-h-96 text-xs" data-testid="config-view" />
        )
      ) : (
        !loadError && <p className="text-xs text-muted-foreground">Loading…</p>
      )}
      {saved && saved.version >= artifact.version && (
        <div className="flex flex-col gap-1.5" data-testid="artifact-saved" role="status">
          <p className="text-xs text-muted-foreground">
            Saved as v{saved.version} in the working set{capabilities?.release?.working_set_dirty ? ', not released yet' : ''}. The diff against v{saved.version - 1}:
          </p>
          <DiffView lines={parseUnifiedDiff(saved.diff)} label={`Diff of ${label} v${saved.version - 1} to v${saved.version}`} />
        </div>
      )}
    </section>
  )
}

/** Keep two lines of context around each change in the preview. */
function near(lines: { type: string }[], i: number): boolean {
  for (let d = -2; d <= 2; d++) if (lines[i + d] && lines[i + d]!.type !== 'same') return true
  return false
}

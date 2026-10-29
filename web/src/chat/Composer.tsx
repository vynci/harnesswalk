import { useState, type KeyboardEvent } from 'react'
import { ChevronRight, SendHorizontal, SlidersHorizontal, Square } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { parseOutputSchema, useApp } from '@/state/store'

const NO_HINT = '__none__'
const EXAMPLE_SCHEMA = '{"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}'

/**
 * Dev mode only: per-turn options the API takes on POST /v1/turns. `output_schema` makes the
 * output guardrails check the answer against a JSON Schema; `model` is a router hint, honoured
 * only when the matching rule allows it.
 */
function TurnOptions() {
  const capabilities = useApp((s) => s.capabilities)
  const outputSchema = useApp((s) => s.outputSchema)
  const setOutputSchema = useApp((s) => s.setOutputSchema)
  const modelHint = useApp((s) => s.modelHint)
  const setModelHint = useApp((s) => s.setModelHint)
  const [open, setOpen] = useState(false)
  if (capabilities?.mode !== 'dev') return null
  const models = [...new Set(capabilities.providers.filter((p) => p.configured).flatMap((p) => p.models))]
  const schemaError = parseOutputSchema(outputSchema).error
  const active = [outputSchema.trim() ? 'schema' : null, modelHint ? `model ${modelHint}` : null].filter(Boolean)
  return (
    <Collapsible open={open} onOpenChange={setOpen} data-testid="turn-options">
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost" size="xs" className="text-muted-foreground" aria-label="Turn options (dev mode)">
          <ChevronRight aria-hidden className={open ? 'rotate-90 transition-transform' : 'transition-transform'} />
          <SlidersHorizontal aria-hidden /> Options{active.length ? `: ${active.join(', ')}` : ''}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5 flex flex-col gap-2 rounded-md border p-2">
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-2">
            Output schema (JSON Schema the answer must satisfy)
            <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => setOutputSchema(EXAMPLE_SCHEMA)}>
              example
            </Button>
          </span>
          <Textarea
            value={outputSchema}
            onChange={(e) => setOutputSchema(e.target.value)}
            rows={2}
            placeholder="Empty: no schema check"
            className="min-h-0 resize-y font-mono text-[11px]"
            aria-invalid={!!schemaError}
            data-testid="output-schema-input"
          />
          {schemaError && <span className="text-destructive">{schemaError}</span>}
        </label>
        <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
          Model hint
          <Select value={modelHint ?? NO_HINT} onValueChange={(v) => setModelHint(v === NO_HINT ? null : v)}>
            <SelectTrigger size="sm" className="h-7 w-40 text-xs" aria-label="Model hint for the router" data-testid="model-hint">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_HINT}>none (router decides)</SelectItem>
              {models.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function Composer({
  busy,
  canStop,
  stopping,
  sending,
  maxChars,
  onSend,
  onStop,
}: {
  busy: boolean
  canStop: boolean
  /** Stop was pressed and the turn has not ended yet. */
  stopping: boolean
  sending: boolean
  maxChars: number
  onSend: (text: string) => Promise<void>
  onStop: () => Promise<void>
}) {
  const [text, setText] = useState('')
  const tooLong = text.length > maxChars
  const canSend = !busy && !sending && text.trim().length > 0 && !tooLong

  const submit = async () => {
    if (!canSend) return
    const value = text
    setText('')
    await onSend(value)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <form
      className="flex flex-col gap-1.5 border-t bg-background p-3"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <Textarea
        id="composer"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={busy ? 'The harness is working on the last message…' : 'Ask something about orders or support hours.'}
        aria-describedby="composer-keys"
        rows={2}
        className="max-h-40 min-h-[3.25rem] resize-none text-sm"
        aria-invalid={tooLong}
        data-testid="composer-input"
      />
      <TurnOptions />
      <div className="flex items-center gap-2">
        <span id="composer-keys" className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <Kbd>Enter</Kbd> sends,
          <KbdGroup>
            <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd>
          </KbdGroup>
          adds a line
        </span>
        <span className={tooLong ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'} aria-live="polite">
          {text.length > maxChars * 0.8 ? `${text.length} / ${maxChars}` : ''}
        </span>
        <span className="ml-auto flex gap-2">
          {busy && canStop && (
            <Hint label="Stops at the next step boundary. A model or tool call already in flight finishes first." side="top">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void onStop()}
                disabled={stopping}
                aria-label={stopping ? 'Stopping the turn' : 'Stop the running turn'}
                data-testid="stop-button"
              >
                <Square aria-hidden className="fill-current" /> {stopping ? 'Stopping…' : 'Stop'}
              </Button>
            </Hint>
          )}
          <Button type="submit" size="sm" disabled={!canSend} aria-label="Send message" data-testid="send-button">
            <SendHorizontal aria-hidden /> Send
          </Button>
        </span>
      </div>
    </form>
  )
}

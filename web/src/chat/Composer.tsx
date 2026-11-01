import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowUp, ChevronDown, Plus, SlidersHorizontal, Square } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupText, InputGroupTextarea } from '@/components/ui/input-group'
import { Kbd, KbdGroup } from '@/components/ui/kbd'
import { Spinner } from '@/components/ui/spinner'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { parseOutputSchema, useApp } from '@/state/store'

const NO_HINT = '__none__'
const EXAMPLE_SCHEMA = '{"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}'

/**
 * Dev mode only: the output schema for the next turn. `output_schema` makes the output
 * guardrails check the answer against a JSON Schema.
 */
function SchemaOption() {
  const outputSchema = useApp((s) => s.outputSchema)
  const setOutputSchema = useApp((s) => s.setOutputSchema)
  const schemaError = parseOutputSchema(outputSchema).error
  return (
    <div id="turn-options" className="flex flex-col gap-1.5 rounded-2xl bg-muted/50 p-3 text-xs text-muted-foreground" data-testid="turn-options">
      <label htmlFor="output-schema" className="flex items-center gap-2">
        Output schema (JSON Schema the answer must satisfy)
        <Button type="button" variant="link" size="xs" className="h-auto p-0" onClick={() => setOutputSchema(EXAMPLE_SCHEMA)}>
          example
        </Button>
      </label>
      <Textarea
        id="output-schema"
        value={outputSchema}
        onChange={(e) => setOutputSchema(e.target.value)}
        rows={2}
        placeholder="Empty: no schema check"
        className="min-h-0 resize-y bg-background font-mono text-xs md:text-xs"
        aria-invalid={!!schemaError}
        data-testid="output-schema-input"
      />
      {schemaError && <span className="text-destructive">{schemaError}</span>}
    </div>
  )
}

/** Dev mode only: the model hint as a Model Selector menu. The router honours it only when its rule allows. */
function ModelHint() {
  const capabilities = useApp((s) => s.capabilities)
  const modelHint = useApp((s) => s.modelHint)
  const setModelHint = useApp((s) => s.setModelHint)
  const models = [...new Set(capabilities?.providers.filter((p) => p.configured).flatMap((p) => p.models) ?? [])]
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <InputGroupButton size="xs" className="text-muted-foreground" aria-label={`Model hint for the router: ${modelHint ?? 'router decides'}`} data-testid="model-hint">
          {modelHint ?? 'Auto model'}
          <ChevronDown aria-hidden />
        </InputGroupButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel>Model hint</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={modelHint ?? NO_HINT} onValueChange={(v) => setModelHint(v === NO_HINT ? null : v)}>
          <DropdownMenuRadioItem value={NO_HINT}>Auto (router decides)</DropdownMenuRadioItem>
          {models.map((m) => (
            <DropdownMenuRadioItem key={m} value={m} className="font-mono">
              {m}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Step mode, on the hint line under the input: pause the next turns at every node. */
function StepModeSwitch() {
  const available = useApp((s) => s.capabilities?.features.step_mode !== false)
  const stepMode = useApp((s) => s.stepMode)
  const setStepMode = useApp((s) => s.setStepMode)
  if (!available) return null
  return (
    <Hint label="Pause at every node. Press Next to move one hop." side="top">
      <label className="flex cursor-pointer items-center gap-1.5 text-xs">
        <Switch checked={stepMode} onCheckedChange={setStepMode} aria-label="Step mode: pause at every node" data-testid="step-toggle" />
        Step mode
      </label>
    </Hint>
  )
}

/**
 * The message input from the AI Elements Chat pattern: an Input Group with the textarea, the plus
 * button on the left (attachments come later) and the arrow-up send on the right, which turns
 * into Stop while a turn runs.
 */
export function Composer({
  busy,
  canStop,
  stopping,
  sending,
  maxChars,
  onSend,
  onStop,
  suggestions,
}: {
  busy: boolean
  canStop: boolean
  /** Stop was pressed and the turn has not ended yet. */
  stopping: boolean
  sending: boolean
  maxChars: number
  onSend: (text: string) => Promise<void>
  onStop: () => Promise<void>
  /** Shown above the input (the Suggestion row of an empty chat). */
  suggestions?: ReactNode
}) {
  // The text lives in the store, so a lesson's "try it" can put its prompt here.
  const text = useApp((s) => s.draft)
  const setText = useApp((s) => s.setDraft)
  const focusRequest = useApp((s) => s.composerFocus)
  const input = useRef<HTMLTextAreaElement>(null)
  const [options, setOptions] = useState(false)
  const dev = useApp((s) => s.capabilities?.mode === 'dev')
  // P5: the pack the next message goes to names the placeholder; the starter pack keeps its own.
  const packName = useApp((s) => {
    const p = s.packs?.find((x) => x.id === s.pack)
    return p && !p.default ? p.title || p.id : null
  })
  const placeholder = packName ? `Ask ${packName} something.` : 'Ask something about orders or support hours.'
  const schemaSet = useApp((s) => s.outputSchema.trim().length > 0)
  const tooLong = text.length > maxChars
  const canSend = !busy && !sending && text.trim().length > 0 && !tooLong

  useEffect(() => {
    if (focusRequest) input.current?.focus()
  }, [focusRequest])

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
      className="flex flex-col gap-2 bg-background px-3 pt-2 pb-3"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      {suggestions}
      {dev && options && <SchemaOption />}
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <InputGroup>
        <InputGroupTextarea
          ref={input}
          id="composer"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={busy ? 'The harness is working on the last message…' : placeholder}
          aria-describedby="composer-keys"
          rows={1}
          className="max-h-40 min-h-14 px-3.5 pt-3 text-sm md:text-sm"
          aria-invalid={tooLong}
          data-testid="composer-input"
        />
        <InputGroupAddon align="block-end" className="gap-1.5 pt-1">
          <Hint label="Attach, coming later" side="top">
            <InputGroupButton
              size="icon-sm"
              variant="outline"
              className="rounded-full aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              aria-disabled="true"
              aria-label="Attach files (coming later)"
              onClick={(e) => e.preventDefault()}
              data-testid="attach-button"
            >
              <Plus />
            </InputGroupButton>
          </Hint>
          {dev && (
            <>
              <InputGroupButton
                size="xs"
                className="text-muted-foreground"
                aria-label="Turn options (dev mode)"
                aria-expanded={options}
                aria-controls="turn-options"
                onClick={() => setOptions(!options)}
              >
                <SlidersHorizontal aria-hidden />
                {schemaSet ? 'Schema' : 'Options'}
              </InputGroupButton>
              <ModelHint />
            </>
          )}
          <span className="ml-auto flex items-center gap-2">
            <InputGroupText className={tooLong ? 'text-xs text-destructive tabular-nums' : 'text-xs tabular-nums'} aria-live="polite">
              {text.length > maxChars * 0.8 ? `${text.length} / ${maxChars}` : ''}
            </InputGroupText>
            {busy && canStop ? (
              <Hint label="Stops at the next step boundary. A model or tool call already in flight finishes first." side="top">
                <InputGroupButton
                  type="button"
                  size="icon-sm"
                  variant="default"
                  className="rounded-full"
                  onClick={() => void onStop()}
                  disabled={stopping}
                  aria-label={stopping ? 'Stopping the turn' : 'Stop the running turn'}
                  data-testid="stop-button"
                >
                  {stopping ? <Spinner role={undefined} aria-hidden className="motion-reduce:animate-none" /> : <Square aria-hidden className="size-3 fill-current" />}
                </InputGroupButton>
              </Hint>
            ) : (
              <InputGroupButton type="submit" size="icon-sm" variant="default" className="rounded-full" disabled={!canSend} aria-label="Send message" data-testid="send-button">
                <ArrowUp />
              </InputGroupButton>
            )}
          </span>
        </InputGroupAddon>
      </InputGroup>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1">
        <p id="composer-keys" className="flex items-center gap-1 text-xs text-muted-foreground">
          <Kbd>Enter</Kbd> sends,
          <KbdGroup>
            <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd>
          </KbdGroup>
          adds a line
        </p>
        <StepModeSwitch />
      </div>
    </form>
  )
}

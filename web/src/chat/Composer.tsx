import { useState, type KeyboardEvent } from 'react'
import { SendHorizontal, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

export function Composer({
  busy,
  canStop,
  sending,
  maxChars,
  onSend,
  onStop,
}: {
  busy: boolean
  canStop: boolean
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
        placeholder={busy ? 'The harness is working on the last message…' : 'Ask something. Enter sends, Shift+Enter adds a line.'}
        rows={2}
        className="max-h-40 min-h-[3.25rem] resize-none text-sm"
        aria-invalid={tooLong}
        data-testid="composer-input"
      />
      <div className="flex items-center gap-2">
        <span className={tooLong ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'} aria-live="polite">
          {text.length > maxChars * 0.8 ? `${text.length} / ${maxChars}` : ''}
        </span>
        <span className="ml-auto flex gap-2">
          {busy && canStop && (
            <Button type="button" size="sm" variant="outline" onClick={() => void onStop()} aria-label="Stop the running turn" data-testid="stop-button">
              <Square aria-hidden className="fill-current" /> Stop
            </Button>
          )}
          <Button type="submit" size="sm" disabled={!canSend} aria-label="Send message" data-testid="send-button">
            <SendHorizontal aria-hidden /> Send
          </Button>
        </span>
      </div>
    </form>
  )
}

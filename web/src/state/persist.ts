// Per-viewer conveniences kept in localStorage. Every access is guarded: storage can be missing
// or throw (private windows, blocked site data) and the app must work without it.
const PREFIX = 'harnesswalk.'
const MAX_MESSAGES = 200

export function load<T>(key: string, fallback: T): T {
  try {
    const raw = globalThis.localStorage?.getItem(PREFIX + key)
    return raw === null || raw === undefined ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

export function save(key: string, value: unknown): void {
  try {
    if (value === null || value === undefined) globalThis.localStorage?.removeItem(PREFIX + key)
    else globalThis.localStorage?.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    // Storage is a convenience; ignore failures.
  }
}

/** The API never returns the user's message, so this browser remembers what it sent. */
export function rememberMessage(turnId: string, text: string): void {
  const map = load<Record<string, string>>('messages', {})
  map[turnId] = text
  const keys = Object.keys(map)
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_MESSAGES))) delete map[k]
  save('messages', map)
}

export function recallMessages(): Record<string, string> {
  return load<Record<string, string>>('messages', {})
}

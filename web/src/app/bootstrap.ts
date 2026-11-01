// Picks the transport (real API or the in-browser mock) and the dev-time validator, then boots
// the store. Both the mock and the validator are dynamic imports behind build-time constants,
// so a production build contains neither.
import { browserTransport, type Transport } from '@/api/client'
import { useApp } from '@/state/store'

export const MOCK = import.meta.env.VITE_HARNESSWALK_MOCK === '1'

export async function bootstrap(): Promise<void> {
  let transport: Transport
  let seedMessages: Record<string, string> | undefined
  let sessionId: string | undefined
  if (MOCK) {
    const { MockServer } = await import('@/mock/server')
    const params = new URLSearchParams(globalThis.location?.search ?? '')
    const stepMs = Number(params.get('mockStepMs') ?? import.meta.env.VITE_HARNESSWALK_MOCK_STEP_MS ?? 140)
    // P6: `?mode=public` serves the public site's capabilities (one Claude model out of budget;
    // `&budget=spent` for both), with `&mockRemaining=` requests left and `&mockTtl=` seconds.
    const isPublic = params.get('mode') === 'public'
    const num = (k: string) => (params.get(k) !== null && Number.isFinite(Number(params.get(k))) ? Number(params.get(k)) : undefined)
    const server = new MockServer({
      stepMs: Number.isFinite(stepMs) ? stepMs : 140,
      ...(isPublic ? { seed: false, public: { budget: params.get('budget') === 'spent' ? 'spent' : 'ok', remaining: num('mockRemaining'), ttlS: num('mockTtl') } } : {}),
    })
    transport = server.transport()
    // A public visitor is a fresh anonymous tenant: no seeded history.
    seedMessages = isPublic ? undefined : server.seededMessages()
    sessionId = isPublic ? undefined : 'sess_demo'
    ;(globalThis as { __harnesswalkMock?: unknown }).__harnesswalkMock = server
  } else {
    transport = browserTransport(import.meta.env.VITE_HARNESSWALK_API_URL ?? '')
  }
  let validate: ((ev: unknown) => string[] | null) | undefined
  if (import.meta.env.DEV || MOCK || import.meta.env.VITE_HARNESSWALK_VALIDATE === '1') {
    const { createEventValidator } = await import('@/api/validate')
    validate = createEventValidator()
  }
  await useApp.getState().boot(transport, { mock: MOCK, validate, seedMessages, sessionId })
}

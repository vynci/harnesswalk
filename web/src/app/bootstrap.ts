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
    const server = new MockServer({ stepMs: Number.isFinite(stepMs) ? stepMs : 140 })
    transport = server.transport()
    seedMessages = server.seededMessages()
    sessionId = 'sess_demo'
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

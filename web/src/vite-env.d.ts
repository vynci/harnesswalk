/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" serves the recorded spec/examples streams from an in-browser mock instead of the API. */
  readonly VITE_HARNESSWALK_MOCK?: string
  /** Milliseconds between mock events (default 140). The ?mockStepMs= query overrides it. */
  readonly VITE_HARNESSWALK_MOCK_STEP_MS?: string
  /** API origin when the UI is not served by the API or the dev proxy; default same origin. */
  readonly VITE_HARNESSWALK_API_URL?: string
  /** "1" validates every streamed event against the schema in a production build too. */
  readonly VITE_HARNESSWALK_VALIDATE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

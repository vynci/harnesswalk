// Runtime validation of each streamed event against spec/events.schema.json. Loaded only in
// dev builds (see app/bootstrap.ts), so production bundles carry neither ajv nor the schema.
import Ajv2020 from 'ajv/dist/2020'
import addFormats from 'ajv-formats'
import schemaText from '@spec/events.schema.json?raw'

export function createEventValidator(): (ev: unknown) => string[] | null {
  const ajv = new Ajv2020({ allErrors: true, strict: false })
  addFormats(ajv)
  const check = ajv.compile(JSON.parse(schemaText) as object)
  return (ev) => {
    if (check(ev)) return null
    return (check.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? 'is invalid'}`)
  }
}

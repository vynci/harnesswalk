// The tuning loop of the in-browser mock (0.2.0, spec/contracts/http_api.md, "0.2.0 additions:
// tuning"), seeded from the recorded run the public server serves (config/fixtures/
// tune_replay.json, read straight from the repo so the two cannot drift). Public mode answers
// every GET from that file with `replay: true` and every POST with 403 forbidden_in_mode, as the
// real server does. Dev mode starts from the same records and acts on them: a curation, a run
// (the fake trainer's recorded steps, streamed), a comparison (the recorded verdict of a model
// that answers like it), promote, the canary gate and rollback.
import type {
  CanaryState,
  Comparison,
  Curation,
  ModelManifest,
  OpsEvent,
  TuneArtifactDetail,
  TuneArtifactRef,
  TuneOverview,
  TuneRun,
  TuneVerify,
} from '@/api/types'
import replayText from '../../../config/fixtures/tune_replay.json?raw'

export interface TuneReplay {
  note: string
  overview: TuneOverview
  artifacts: TuneArtifactRef[]
  artifact_details: Record<string, TuneArtifactDetail>
  curations: (Curation & { events: OpsEvent[] })[]
  runs: (TuneRun & { events: OpsEvent[] })[]
  comparisons: Comparison[]
  canary: CanaryState
  verify: TuneVerify
  story: Record<string, unknown>[]
}

/** The recorded loop, parsed once. */
export const TUNE_REPLAY: TuneReplay = JSON.parse(replayText) as TuneReplay

const TENANT = 'dev'

interface Job {
  kind: 'curation' | 'run'
  record: Curation | TuneRun
  events: OpsEvent[]
  done: boolean
  listeners: Set<(ev: OpsEvent) => void>
}

/** What the tune mock needs from the rest of the mock. */
export interface TuneHost {
  stepMs: number
  public: boolean
  /** Put an event on the tenant's ops stream (GET /v1/events). */
  publish: (type: OpsEvent['type'], nodeId: string, summary: Record<string, unknown>, extra?: Partial<OpsEvent>) => void
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function err(status: number, code: string, message: string, extra: Record<string, unknown> = {}): Response {
  return json(status, { error: { code, message }, ...extra })
}

const clone = <T>(x: T): T => structuredClone(x)

function parseRef(value: string, kind: string): { name: string; version: number | null } | null {
  const m = value.trim().match(new RegExp(`^(?:${kind}/)?([a-z][a-z0-9_]{0,63})(?:@v(\\d+))?$`))
  return m ? { name: m[1]!, version: m[2] ? Number(m[2]) : null } : null
}

export class MockTune {
  private host: TuneHost
  private artifacts: TuneArtifactRef[]
  private details: Record<string, TuneArtifactDetail>
  private curations: Curation[]
  private runs: TuneRun[]
  private comparisons: Comparison[]
  private canaryState: CanaryState
  private jobs = new Map<string, Job>()
  private running: { id: string; kind: 'curation' | 'run' | 'comparison' } | null = null
  private counter = { cu: 0, tr: 0, cmp: 0 }

  constructor(host: TuneHost) {
    this.host = host
    const r = clone(TUNE_REPLAY)
    this.artifacts = r.artifacts
    this.details = r.artifact_details
    this.curations = r.curations.map(({ events, ...c }) => {
      this.jobs.set(c.id, { kind: 'curation', record: c, events, done: true, listeners: new Set() })
      return c
    })
    this.runs = r.runs.map(({ events, ...run }) => {
      this.jobs.set(run.id, { kind: 'run', record: run, events, done: true, listeners: new Set() })
      return run
    })
    this.comparisons = r.comparisons
    this.canaryState = r.canary
    this.counter = { cu: this.curations.length, tr: this.runs.length, cmp: this.comparisons.length }
  }

  /** A public server's answer: the recorded file as it is, marked as a replay. */
  private replayed(): TuneReplay {
    return TUNE_REPLAY
  }

  handle(method: string, url: URL, body: Record<string, unknown>): Response | null {
    const p = url.pathname
    if (!p.startsWith('/v1/tune')) return null
    if (method === 'POST') {
      if (this.host.public) return err(403, 'forbidden_in_mode', 'This is not offered in public mode: the sandbox never tunes, it replays a recorded run.')
      return this.post(p, body)
    }
    if (method !== 'GET') return err(405, 'method_not_allowed', `${method} ${p}`)
    return this.host.public ? this.getReplay(p, url) : this.get(p, url)
  }

  // ---------- GET ----------

  private getReplay(p: string, url: URL): Response {
    const r = this.replayed()
    let m: RegExpMatchArray | null
    if (p === '/v1/tune') return json(200, { ...r.overview, replay: true })
    if (p === '/v1/tune/artifacts') {
      const kind = url.searchParams.get('kind')
      return json(200, { artifacts: r.artifacts.filter((a) => !kind || a.kind === kind), replay: true })
    }
    if ((m = p.match(/^\/v1\/tune\/artifacts\/([^/]+)\/([^/]+)$/))) {
      const found = r.artifact_details[`${m[1]}/${m[2]}`]
      return found ? json(200, { ...found, replay: true }) : err(404, 'artifact_not_found', `No ${m[1]}/${m[2]} in the replay.`)
    }
    if (p === '/v1/tune/verify') return json(200, { ...r.verify, replay: true })
    if (p === '/v1/tune/canary') return json(200, { ...r.canary, replay: true })
    for (const [path, items] of [
      ['curations', r.curations],
      ['runs', r.runs],
      ['comparisons', r.comparisons],
    ] as const) {
      if (p === `/v1/tune/${path}`) return json(200, { [path]: items, replay: true })
      if ((m = p.match(new RegExp(`^/v1/tune/${path}/([^/]+)$`)))) {
        const found = (items as { id: string }[]).find((i) => i.id === m![1])
        return found ? json(200, { ...found, replay: true }) : err(404, 'not_found', `No ${path} ${m[1]} in the replay.`)
      }
    }
    return err(404, 'not_found', `No route GET ${p} in the mock.`)
  }

  private get(p: string, url: URL): Response {
    let m: RegExpMatchArray | null
    if (p === '/v1/tune') return json(200, { ...TUNE_REPLAY.overview, replay: false, canary: this.canary(), running: this.running })
    if (p === '/v1/tune/artifacts') {
      const kind = url.searchParams.get('kind')
      return json(200, { artifacts: this.artifacts.filter((a) => !kind || a.kind === kind) })
    }
    if ((m = p.match(/^\/v1\/tune\/artifacts\/([^/]+)\/([^/]+)$/))) {
      const found = this.details[`${m[1]}/${m[2]}`]
      if (!found) return err(404, 'artifact_not_found', `No ${m[1]}/${m[2]}.`)
      const version = Number(url.searchParams.get('version'))
      if (version && version !== found.version) {
        const h = found.history.find((x) => x.version === version)
        if (!h) return err(404, 'artifact_not_found', `No ${m[1]}/${m[2]}@v${version}.`)
        return json(200, { ...found, ...h, diff_from_previous: '' })
      }
      return json(200, found)
    }
    if (p === '/v1/tune/verify') return json(200, { ok: true, versions: this.artifacts.reduce((n, a) => n + a.version, 0), problems: [] })
    if (p === '/v1/tune/canary') return json(200, this.canary())
    const lists = { curations: this.curations, runs: this.runs, comparisons: this.comparisons } as const
    for (const [path, items] of Object.entries(lists)) {
      if (p === `/v1/tune/${path}`) return json(200, { [path]: items })
      if ((m = p.match(new RegExp(`^/v1/tune/${path}/([^/]+)$`)))) {
        const found = (items as { id: string }[]).find((i) => i.id === m![1])
        return found ? json(200, found) : err(404, 'not_found', `No ${path.slice(0, -1)} ${m[1]}.`)
      }
    }
    return err(404, 'not_found', `No route GET ${p} in the mock.`)
  }

  canary(): CanaryState {
    return clone(this.canaryState)
  }

  // ---------- POST ----------

  private post(p: string, body: Record<string, unknown>): Response {
    if (p === '/v1/tune/promote') return this.promote(body)
    if (p === '/v1/tune/rollback') return this.rollback()
    if (this.running) return err(409, 'tune_running', `${this.running.kind} ${this.running.id} is running`)
    if (p === '/v1/tune/datasets') return this.startCuration(body)
    if (p === '/v1/tune/runs') return this.startRun(body)
    if (p === '/v1/tune/comparisons') return this.startComparison(body)
    return err(404, 'not_found', `No route POST ${p} in the mock.`)
  }

  private started(kind: 'curation' | 'run' | 'comparison', id: string): Response {
    const path = { curation: 'curations', run: 'runs', comparison: 'comparisons' }[kind]
    return json(202, { id, kind, status: 'running', url: `/v1/tune/${path}/${id}`, ...(kind === 'comparison' ? {} : { events_url: `/v1/tune/${path}/${id}/events` }) })
  }

  private startCuration(body: Record<string, unknown>): Response {
    const name = String(body.name ?? '')
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(name)) return err(400, 'invalid_request', 'name must match ^[a-z][a-z0-9_]{0,63}$')
    const packs = Array.isArray(body.packs) ? body.packs.map(String) : []
    if (!packs.length) return err(400, 'invalid_request', 'packs must name a pack')
    const template = TUNE_REPLAY.curations[0]!
    const id = `cu_${++this.counter.cu}`
    const record: Curation = {
      ...clone(template),
      events: undefined,
      id,
      status: 'running',
      created_at: new Date().toISOString(),
      ended_at: null,
      stream: `curate:${id}`,
      request: { ...template.request, name, packs, teacher: String(body.teacher ?? template.request.teacher), ...(typeof body.variants === 'number' ? { variants: body.variants } : {}) },
      counts: null,
      dataset: null,
    }
    delete record.events
    this.curations.unshift(record)
    this.play('curation', record, template.events, null, () => {
      const detail = this.bumpArtifact('dataset', name, (m) => ({ ...m, name, packs, teacher: record.request.teacher, curation_id: id }), `curated by ${id}`, template.dataset!.name)
      record.counts = clone(template.counts ?? null)
      record.dataset = { name, version: detail.version, hash: detail.hash }
      return { dataset: `dataset/${name}@v${detail.version}`, kept: record.counts?.kept ?? 0 }
    })
    return this.started('curation', id)
  }

  private startRun(body: Record<string, unknown>): Response {
    const ref = parseRef(String(body.dataset ?? ''), 'dataset')
    const dataset = ref ? this.details[`dataset/${ref.name}`] : undefined
    if (!ref || !dataset) return err(404, 'artifact_not_found', `No dataset ${String(body.dataset ?? '')}.`)
    const trainer = TUNE_REPLAY.overview.trainers.find((t) => t.alias === String(body.trainer ?? 'fake'))
    if (!trainer) return err(400, 'invalid_request', `unknown trainer ${String(body.trainer)}`)
    if (!trainer.implemented) return err(422, 'trainer_unavailable', `trainer ${trainer.alias} is not implemented`)
    const student = String(body.student ?? '')
    if (!student) return err(400, 'invalid_request', 'student is required')
    const config = (body.config && typeof body.config === 'object' ? body.config : {}) as Record<string, unknown>
    const forgets = config.outcome === 'forgets'
    const name = String(body.name || `${ref.name}_${student.replace(/-/g, '')}`).slice(0, 64)
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(name)) return err(400, 'invalid_request', 'name must match ^[a-z][a-z0-9_]{0,63}$')
    const template = TUNE_REPLAY.runs.find((r) => (r.request.config?.outcome === 'forgets') === forgets) ?? TUNE_REPLAY.runs[0]!
    const id = `tr_${++this.counter.tr}`
    const record: TuneRun = {
      ...clone(template),
      id,
      status: 'running',
      created_at: new Date().toISOString(),
      ended_at: null,
      stream: `tune:${id}`,
      request: { ...template.request, dataset: { name: dataset.name, version: dataset.version, hash: dataset.hash }, trainer: trainer.alias, student, name, config },
      steps: [],
      last_step: null,
      adapter: null,
      model: null,
      metrics: null,
    }
    delete record.events
    this.runs.unshift(record)
    const templateAlias = forgets ? 'forgets_fake' : 'approvals_fake'
    const onStep = (ev: OpsEvent) => {
      if (ev.type !== 'tune_step') return
      const step = ev.summary as unknown as NonNullable<TuneRun['last_step']>
      record.steps = [...(record.steps ?? []), step]
      record.last_step = step
    }
    this.play('run', record, template.events, onStep, () => {
      const adapter = this.bumpArtifact('adapter', name, (m) => ({ ...m, name, run_id: id, student, trainer: trainer.alias }), `trained by ${id}`, templateAlias)
      const model = this.bumpArtifact(
        'model',
        name,
        (m) => ({ ...m, name, run_id: id, student, alias: `tuned-${name.replace(/_/g, '-')}`, adapter: { name, version: adapter.version, hash: adapter.hash }, gate: null }),
        `served model of ${id}`,
        templateAlias,
      )
      record.adapter = { name, version: adapter.version, hash: adapter.hash }
      record.model = { name, version: model.version, hash: model.hash }
      record.metrics = clone(template.metrics ?? null)
      return { adapter: `adapter/${name}@v${adapter.version}`, model: `model/${name}@v${model.version}`, metrics: record.metrics }
    })
    return this.started('run', id)
  }

  private startComparison(body: Record<string, unknown>): Response {
    const ref = parseRef(String(body.candidate ?? ''), 'model')
    const model = ref ? this.details[`model/${ref.name}`] : undefined
    if (!ref || !model) return err(404, 'artifact_not_found', `No model ${String(body.candidate ?? '')}.`)
    const against = String(body.against ?? '')
    if (!against) return err(400, 'invalid_request', 'against (the base alias) is required')
    const packs = Array.isArray(body.packs) ? body.packs.map(String) : String(body.packs ?? '').split(',').filter(Boolean)
    const canaryPacks = Array.isArray(body.canary_packs) ? body.canary_packs.map(String) : String(body.canary_packs ?? '').split(',').filter(Boolean)
    if (!packs.length) return err(400, 'invalid_request', 'packs (the target) must name a pack')
    const forgets = (model.manifest as ModelManifest).modelfile?.includes('forgets')
    const template = TUNE_REPLAY.comparisons.find((c) => c.request.candidate.name === (forgets ? 'forgets_fake' : 'approvals_fake')) ?? TUNE_REPLAY.comparisons[0]!
    const id = `cmp_${++this.counter.cmp}`
    const record: Comparison = {
      ...clone(template),
      id,
      status: 'running',
      created_at: new Date().toISOString(),
      ended_at: null,
      request: { ...template.request, candidate: { name: model.name, version: model.version, hash: model.hash }, against, packs, canary_packs: canaryPacks, samples: Number(body.samples) || template.request.samples },
      cells: null,
      verdict: null,
    }
    this.comparisons.unshift(record)
    this.running = { id, kind: 'comparison' }
    setTimeout(() => {
      record.cells = clone(template.cells ?? null)
      record.verdict = { ...clone(template.verdict!), comparison_id: id, against, at: new Date().toISOString() }
      record.status = 'done'
      record.ended_at = new Date().toISOString()
      const gated = this.bumpArtifact('model', model.name, (m) => ({ ...m, gate: record.verdict }), `gate ${record.verdict.status} by ${id}`)
      record.model = { name: model.name, version: gated.version, hash: gated.hash }
      this.running = null
      const v = record.verdict
      this.host.publish('release_gated', 'release', { pack: packs[0], gate: v.status, suite: 'model', reason: v.reason, packs, canary_packs: canaryPacks, candidate: `model/${model.name}@v${gated.version}`, gain: v.gain, canary_drop: v.canary_drop, prompt_arm: v.prompt_arm }, { status: v.status === 'failed' ? 'error' : 'done' })
    }, Math.max(20, this.host.stepMs * 4))
    return this.started('comparison', id)
  }

  private promote(body: Record<string, unknown>): Response {
    if (this.running) return err(409, 'tune_running', 'a curation, run or comparison is running')
    const stage = String(body.stage ?? 'canary')
    if (stage !== 'canary' && stage !== 'full') return err(400, 'invalid_request', 'stage must be canary or full')
    const ref = parseRef(String(body.candidate ?? ''), 'model')
    const detail = ref ? this.details[`model/${ref.name}`] : undefined
    if (!ref || !detail) return err(404, 'artifact_not_found', `No model ${String(body.candidate ?? '')}.`)
    const model = detail.manifest as ModelManifest
    const gate = model.gate ?? {}
    const force = body.force === true
    if ((gate as { status?: string }).status !== 'passed' && !force) return err(409, 'gate_failed', `model/${ref.name} has no passed gate`, { gate })
    const packs = (Array.isArray(body.packs) && body.packs.length ? body.packs.map(String) : (model.gate?.packs ?? [])) as string[]
    const against = String(body.against ?? model.gate?.against ?? model.student ?? '')
    let fraction: number
    if (stage === 'full') {
      const c = this.canaryState
      if (c.rule?.split.candidate_alias !== model.alias) return err(409, 'canary_failed', 'no canary of this model is running')
      if (!c.gate.passed && !force) return err(409, 'canary_failed', c.gate.reason ?? 'the canary gate did not pass', { canary: this.canary() })
      fraction = 1
    } else {
      fraction = typeof body.fraction === 'number' ? body.fraction : 0.1
      if (!(fraction >= 0 && fraction <= 1)) return err(400, 'invalid_request', 'fraction must be from 0 to 1')
    }
    return json(200, this.setCanary({ stage: stage as 'canary' | 'full', model: `model/${ref.name}@v${detail.version}`, fraction, packs, base_alias: against, candidate_alias: model.alias, by: `user:${TENANT}`, forced: force }))
  }

  private rollback(): Response {
    if (this.running) return err(409, 'tune_running', 'a curation, run or comparison is running')
    const rule = this.canaryState.rule
    if (!rule) return err(409, 'no_canary', 'no canary rule is set')
    const packs = Array.isArray(rule.when?.pack) ? rule.when.pack.map(String) : rule.when?.pack ? [String(rule.when.pack)] : []
    return json(200, this.setCanary({ stage: 'rollback', fraction: 0, packs, base_alias: rule.split.base_alias, candidate_alias: rule.split.candidate_alias, by: `user:${TENANT}` }))
  }

  private setCanary(setting: CanaryState['history'][number]) {
    const at = new Date().toISOString()
    const s = { ...setting, at }
    const fresh = { turns: 0, failed: 0, bad_feedback: 0, failure_rate: null }
    this.canaryState = {
      rule: { when: { pack: setting.packs }, split: { base_alias: setting.base_alias, candidate_alias: setting.candidate_alias, fraction: setting.fraction } },
      last: s,
      arms: { base: { ...fresh }, candidate: { ...fresh } },
      gate: { passed: false, reason: 'too_few_turns: 0 of 5' },
      history: [s, ...this.canaryState.history].slice(0, 10),
    }
    const releases = setting.packs.map((pack, i) => ({ pack, release_id: `rt${this.canaryState.history.length}_${i + 1}`, gate: 'skipped' }))
    this.host.publish('artifact_changed', 'release', { kind: 'models', name: 'models', version: this.canaryState.history.length + 1, note: `tune ${setting.stage}` })
    return { releases, setting: s, canary: this.canary() }
  }

  /** Mock helper: count canary turns for an arm (the e2e and unit tests drive the canary gate). */
  addCanaryTurns(arm: 'base' | 'candidate', turns: number, bad = 0) {
    const a = this.canaryState.arms[arm]
    a.turns += turns
    a.failed += bad
    a.bad_feedback += bad
    a.failure_rate = a.turns ? Number((a.failed / a.turns).toFixed(4)) : null
    const cand = this.canaryState.arms.candidate
    const base = this.canaryState.arms.base
    if (cand.turns < 5) this.canaryState.gate = { passed: false, reason: `too_few_turns: ${cand.turns} of 5` }
    else if ((cand.failure_rate ?? 0) - (base.failure_rate ?? 0) > 0.1) this.canaryState.gate = { passed: false, reason: `candidate_worse: failure rate ${cand.failure_rate} against ${base.failure_rate ?? 0}` }
    else this.canaryState.gate = { passed: true, reason: null }
  }

  /** A new version of a tune artifact, from the latest of `from` (or itself), with its history. */
  private bumpArtifact(kind: 'dataset' | 'adapter' | 'model', name: string, edit: (m: Record<string, unknown>) => Record<string, unknown>, note: string, from = name): TuneArtifactDetail {
    const key = `${kind}/${name}`
    const existing = this.details[key]
    const source = existing ?? this.details[`${kind}/${from}`]!
    const version = existing ? existing.version + 1 : 1
    const manifest = edit(clone(source.manifest) as unknown as Record<string, unknown>)
    const at = new Date().toISOString()
    const hashText = `${key}@${version}:${at}`
    let h = 0x811c9dc5
    for (let i = 0; i < hashText.length; i++) h = Math.imul(h ^ hashText.charCodeAt(i), 0x01000193) >>> 0
    const hash = h.toString(16).padStart(8, '0').repeat(8)
    const row = { kind, name, version, hash, note, updated_at: at, updated_by: `user:${TENANT}` }
    const previous = existing?.manifest
    const detail: TuneArtifactDetail = {
      ...row,
      manifest: manifest as unknown as TuneArtifactDetail['manifest'],
      history: [row, ...(existing?.history ?? [])],
      diff_from_previous: previous ? `--- a/${key}.json\n+++ b/${key}.json\n@@ -1 +1 @@\n-${JSON.stringify(previous).slice(0, 80)}\n+${JSON.stringify(manifest).slice(0, 80)}\n` : '',
    }
    this.details[key] = detail
    const summary =
      kind === 'model'
        ? { alias: (manifest as { alias?: string }).alias, gate: ((manifest as { gate?: { status?: string } | null }).gate ?? null)?.status ?? null, serve: (manifest as { serve?: unknown }).serve }
        : kind === 'dataset'
          ? { teacher: manifest.teacher, packs: manifest.packs, ...(TUNE_REPLAY.artifacts.find((a) => a.kind === 'dataset')?.summary ?? {}) }
          : { student: manifest.student, trainer: manifest.trainer, metrics: manifest.metrics }
    const ref: TuneArtifactRef = { ...row, summary: summary as Record<string, unknown> }
    this.artifacts = [ref, ...this.artifacts.filter((a) => !(a.kind === kind && a.name === name))]
    this.host.publish('artifact_changed', kind === 'dataset' ? 'curate' : 'tune', { kind, name, version })
    return detail
  }

  /**
   * Play a job's recorded events with fresh timestamps onto its stream and the ops stream; the
   * last one is the job's end, whose summary `finish` fills in (it records the artifacts).
   */
  private play(kind: 'curation' | 'run', record: Curation | TuneRun, template: OpsEvent[], onStep: ((ev: OpsEvent) => void) | null, finish: () => Record<string, unknown>) {
    const job: Job = { kind, record, events: [], done: false, listeners: new Set() }
    this.jobs.set(record.id, job)
    this.running = { id: record.id, kind }
    const delay = Math.max(1, Math.min(this.host.stepMs, Math.floor(1500 / Math.max(1, template.length))))
    let i = 0
    const next = () => {
      const t = template[i]!
      const last = i === template.length - 1
      const ev: OpsEvent = { ...clone(t), event_id: `evt_${record.id}_${i}`, seq: i, ts: new Date().toISOString(), summary: { ...t.summary, run_id: record.id } }
      onStep?.(ev)
      if (last) {
        ev.summary = { ...ev.summary, ...finish(), status: 'done' }
        record.status = 'done'
        record.ended_at = new Date().toISOString()
        this.running = null
        job.done = true
      }
      job.events.push(ev)
      this.host.publish(ev.type, ev.node_id ?? '', ev.summary ?? {}, { status: ev.status, stream: record.stream })
      for (const l of job.listeners) l(ev)
      i++
      if (!last) setTimeout(next, delay)
    }
    setTimeout(next, delay)
  }

  /** Follow a job's stream: past events first, then live ones; null for an unknown job. */
  subscribe(id: string, listener: (ev: OpsEvent) => void): { unsubscribe: () => void; done: boolean } | null {
    const job = this.jobs.get(id)
    if (!job) return null
    for (const ev of job.events) listener(ev)
    if (job.done) return { unsubscribe: () => {}, done: true }
    job.listeners.add(listener)
    return { unsubscribe: () => job.listeners.delete(listener), done: false }
  }
}

/** EventSource for /v1/tune/runs/{id}/events and /v1/tune/curations/{id}/events. */
export class MockTuneEventSource {
  readyState = 0
  url: string
  onopen: ((this: EventSource, ev: Event) => unknown) | null = null
  onerror: ((this: EventSource, ev: Event) => unknown) | null = null
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null
  private listeners = new Map<string, Set<(ev: MessageEvent) => void>>()
  private unsubscribe: (() => void) | null = null

  constructor(tune: MockTune, url: string) {
    this.url = url
    const m = new URL(url, 'http://mock.local').pathname.match(/^\/v1\/tune\/(?:runs|curations)\/([^/]+)\/events$/)
    const id = m ? decodeURIComponent(m[1]!) : ''
    setTimeout(() => {
      if (this.readyState === 2) return
      this.readyState = 1
      this.onopen?.call(this as unknown as EventSource, new Event('open'))
      const sub = tune.subscribe(id, (ev) => this.deliver(ev))
      if (!sub) return this.fail()
      this.unsubscribe = sub.unsubscribe
      if (sub.done) setTimeout(() => this.fail(), 0)
    }, 0)
  }

  static matches(path: string): boolean {
    return /^\/v1\/tune\/(?:runs|curations)\/[^/]+\/events$/.test(path)
  }

  private deliver(ev: OpsEvent) {
    if (this.readyState !== 1) return
    const msg = new MessageEvent(ev.type, { data: JSON.stringify(ev), lastEventId: String(ev.seq ?? '') })
    const set = this.listeners.get(ev.type)
    if (set?.size) for (const l of set) l(msg)
    else this.onmessage?.call(this as unknown as EventSource, msg)
    if (ev.type === 'tune_run_finished' || ev.type === 'eval_run_finished') setTimeout(() => this.fail(), 0)
  }

  private fail() {
    if (this.readyState === 2) return
    this.readyState = 2
    this.unsubscribe?.()
    this.onerror?.call(this as unknown as EventSource, new Event('error'))
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(listener)
  }

  removeEventListener(type: string, listener: (ev: MessageEvent) => void) {
    this.listeners.get(type)?.delete(listener)
  }

  close() {
    this.readyState = 2
    this.unsubscribe?.()
  }
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, datasheetUrl } from '../lib/api'
import { dateLabel, num } from '../lib/format'
import {
  MATERIAL_FORMS,
  TDS_SPEC_KEYS,
  type ApplicationFit,
  type ApplicationProfile,
  type MaterialFormKind,
  type ProductSimilarity,
  type TdsMatchRecord,
  type TdsMatchReport,
  type TdsParameter,
  type TdsSpecInput,
  type TdsSpecKey,
  type TdsTemplate,
} from '../lib/types'
import { Banner, Button, Card, Empty, Field, Input, Select, Spinner, Tag, VerdictBadge } from '../components/ui'

type SpecText = Partial<Record<TdsSpecKey, string>>

interface Draft {
  name: string
  form: MaterialFormKind
  synthesis: string
  surface_chemistry: string
  orientation: string
  crystallinity: string
  solubility: string
  solvent: string
  spec: SpecText
}

const EMPTY_DRAFT: Draft = {
  name: '',
  form: 'unknown',
  synthesis: '',
  surface_chemistry: '',
  orientation: '',
  crystallinity: '',
  solubility: '',
  solvent: '',
  spec: {},
}

// The order the sheet lists its characteristics, so the form reads like the TDS.
const SHEET_ROWS: TdsSpecKey[] = [
  'id_ig',
  'layers',
  'thickness_nm',
  'lateral_size_um',
  'carbon_purity_pct',
  'hydrogen_pct',
  'oxygen_pct',
  'impurities_pct',
  'bet_m2_g',
  'bulk_density_g_cm3',
  'electrical_conductivity_s_m',
  'thermal_conductivity_w_mk',
  'thermal_stability_c',
]
const LIQUID_ROWS: TdsSpecKey[] = ['loading_wt_pct', 'density_g_ml', 'viscosity_cps', 'sheet_resistance_ohm_sq']
const INFORMATIONAL: Set<string> = new Set(['hydrogen_pct', 'thermal_stability_c'])

const CONFIDENCE_LABEL = { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence' }

function formLabelOf(form: string): string {
  return MATERIAL_FORMS.find((f) => f.value === form)?.label ?? form
}

function draftFromInput(input: TdsSpecInput): Draft {
  const spec: SpecText = {}
  for (const key of TDS_SPEC_KEYS) {
    const value = input[key]
    if (value !== null && value !== undefined && value !== '') spec[key] = String(value)
  }
  return {
    name: input.name ?? '',
    form: input.form ?? 'unknown',
    synthesis: input.synthesis ?? '',
    surface_chemistry: input.surface_chemistry ?? '',
    orientation: input.orientation ?? '',
    crystallinity: input.crystallinity ?? '',
    solubility: input.solubility ?? '',
    solvent: input.solvent ?? '',
    spec,
  }
}

function inputFromDraft(draft: Draft): TdsSpecInput {
  const body: TdsSpecInput = {
    name: draft.name || null,
    form: draft.form,
    synthesis: draft.synthesis || null,
    surface_chemistry: draft.surface_chemistry || null,
    orientation: draft.orientation || null,
    crystallinity: draft.crystallinity || null,
    solubility: draft.solubility || null,
    solvent: draft.solvent || null,
  }
  for (const key of TDS_SPEC_KEYS) {
    const value = draft.spec[key]?.trim()
    if (value) body[key] = value
  }
  return body
}

function countEntered(spec: SpecText): number {
  return Object.values(spec).filter((v) => v && v.trim()).length
}

function SpecRow({
  specKey,
  meta,
  method,
  value,
  sheetValue,
  onChange,
}: {
  specKey: TdsSpecKey
  meta: TdsParameter
  method: string | undefined
  value: string
  sheetValue: string | undefined
  onChange: (value: string) => void
}) {
  const onSheet = sheetValue !== undefined
  const changed = onSheet && sheetValue !== value
  return (
    <tr className="border-t border-[color:var(--border)]">
      <td className="py-1.5 pr-2 align-top">
        <p className="text-[13px] font-medium text-ink">{meta.label}</p>
        <p className="text-[11px] text-ink-muted">
          {method ?? meta.hint}
          {INFORMATIONAL.has(specKey) && ' · recorded, not scored'}
        </p>
      </td>
      <td className="py-1.5 pr-2 align-top">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={onSheet ? '' : 'not on sheet'}
          className={changed ? 'border-[color:var(--series-1)]' : undefined}
          aria-label={meta.label}
        />
      </td>
      <td className="tabular w-14 py-1.5 align-top text-[12px] text-ink-muted">{meta.unit}</td>
    </tr>
  )
}

export function ApplicationFinder() {
  const [parameters, setParameters] = useState<TdsParameter[] | null>(null)
  const [profiles, setProfiles] = useState<ApplicationProfile[] | null>(null)
  const [template, setTemplate] = useState<TdsTemplate | null>(null)
  const [history, setHistory] = useState<TdsMatchRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [report, setReport] = useState<TdsMatchReport | null>(null)
  const [baseline, setBaseline] = useState<TdsMatchReport | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [tab, setTab] = useState<'results' | 'market'>('results')
  const abortRef = useRef<AbortController | null>(null)

  // Load reference data, the user's own TDS, and the saved matches.
  useEffect(() => {
    Promise.all([api.tdsParameters(), api.tdsApplications(), api.tdsMatches(), api.tdsTemplate().catch(() => null)])
      .then(([params, apps, matches, tmpl]) => {
        setParameters(params)
        setProfiles(apps)
        setHistory(matches)
        setTemplate(tmpl)
        if (tmpl) {
          const initial = draftFromInput(tmpl)
          setDraft(initial)
          api
            .tdsPreview(inputFromDraft(initial))
            .then((base) => {
              setBaseline(base)
              setReport(base)
            })
            .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
        }
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
  }, [])

  const entered = useMemo(() => countEntered(draft.spec), [draft.spec])
  const templateDraft = useMemo(() => (template ? draftFromInput(template) : null), [template])
  const differsFromSheet = useMemo(
    () => templateDraft !== null && JSON.stringify(templateDraft) !== JSON.stringify(draft),
    [templateDraft, draft],
  )

  // Live re-ranking: debounce edits, cancel the request in flight.
  const runPreview = useCallback((next: Draft) => {
    abortRef.current?.abort()
    if (countEntered(next.spec) === 0) {
      setReport(null)
      setPreviewing(false)
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setPreviewing(true)
    api
      .tdsPreview(inputFromDraft(next), controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setReport(result)
          setSavedId(null)
          setError(null)
        }
      })
      .catch((cause) => {
        if (controller.signal.aborted) return
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewing(false)
      })
  }, [])

  const debounceRef = useRef<number | null>(null)
  function update(patch: Partial<Draft> | ((d: Draft) => Draft)) {
    setDraft((current) => {
      const next = typeof patch === 'function' ? patch(current) : { ...current, ...patch }
      if (debounceRef.current) window.clearTimeout(debounceRef.current)
      debounceRef.current = window.setTimeout(() => runPreview(next), 450)
      return next
    })
  }
  function setSpec(key: TdsSpecKey, value: string) {
    update((d) => ({ ...d, spec: { ...d.spec, [key]: value } }))
  }

  function resetToSheet() {
    if (!templateDraft) return
    update(templateDraft)
  }

  function loadRecord(item: TdsMatchRecord) {
    const next = draftFromInput(item.input)
    setDraft(next)
    setReport(item.report)
    setSavedId(item.id)
    setTab('results')
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const created = await api.tdsMatch(inputFromDraft(draft))
      setHistory((current) => [created, ...(current ?? [])])
      setReport(created.report)
      setSavedId(created.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  async function remove(item: TdsMatchRecord) {
    try {
      await api.deleteTdsMatch(item.id)
      setHistory((current) => (current ?? []).filter((m) => m.id !== item.id))
      if (savedId === item.id) setSavedId(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  if (!parameters || !profiles) {
    return (
      <div className="flex items-center justify-center py-20">
        {error ? <Banner kind="error">{error}</Banner> : <Spinner label="Loading the application database…" />}
      </div>
    )
  }

  const byKey = new Map(parameters.map((p) => [p.key, p]))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold text-ink">Application finder</h1>
          <p className="text-[13px] text-ink-secondary">
            Your TDS is the input. Edit any value as the sheet quotes it (&ldquo;130-170&rdquo;, &ldquo;&lt;0.5&rdquo;,
            &ldquo;&gt;99&rdquo;) and the ranking re-computes as you type, against {report?.database_size ?? '…'}{' '}
            commercial grades and the applications they are sold into.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant={tab === 'results' ? 'secondary' : 'ghost'} onClick={() => setTab('results')}>
            Matches
          </Button>
          <Button size="sm" variant={tab === 'market' ? 'secondary' : 'ghost'} onClick={() => setTab('market')}>
            Market database
          </Button>
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}

      <div className="grid gap-4 lg:grid-cols-[420px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card
            title={template ? `${template.company ?? 'Your'} TDS` : 'Your TDS'}
            subtitle={
              template?.source_file
                ? `Prefilled from ${template.source_file}. Edit any value; blue border marks a change from the sheet.`
                : 'Enter the values from your technical data sheet'
            }
            actions={
              template && (
                <Button size="sm" variant="ghost" onClick={resetToSheet} disabled={!differsFromSheet}>
                  Reset to sheet
                </Button>
              )
            }
          >
            <div className="space-y-3">
              <Field label="Product">
                <Input value={draft.name} onChange={(e) => update({ name: e.target.value })} placeholder="Product name" />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Form">
                  <Select value={draft.form} onChange={(e) => update({ form: e.target.value as MaterialFormKind })}>
                    {MATERIAL_FORMS.map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Synthesis">
                  <Input value={draft.synthesis} onChange={(e) => update({ synthesis: e.target.value })} />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Functionalization">
                  <Input
                    value={draft.surface_chemistry}
                    onChange={(e) => update({ surface_chemistry: e.target.value })}
                    placeholder="e.g. none, -COOH"
                  />
                </Field>
                <Field label="Orientation">
                  <Input value={draft.orientation} onChange={(e) => update({ orientation: e.target.value })} />
                </Field>
              </div>

              <table className="w-full">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-ink-muted">
                    <th className="pb-1 font-medium">Characteristic · method</th>
                    <th className="pb-1 font-medium">Value</th>
                    <th className="pb-1 font-medium">Unit</th>
                  </tr>
                </thead>
                <tbody>
                  {SHEET_ROWS.map((key) => {
                    const meta = byKey.get(key)
                    return meta ? (
                      <SpecRow
                        key={key}
                        specKey={key}
                        meta={meta}
                        method={template?.methods?.[key]}
                        value={draft.spec[key] ?? ''}
                        sheetValue={templateDraft?.spec[key]}
                        onChange={(value) => setSpec(key, value)}
                      />
                    ) : null
                  })}
                </tbody>
              </table>

              <details className="rounded-md border border-[color:var(--border)] px-3 py-2">
                <summary className="cursor-pointer text-[12px] font-medium text-ink-secondary">
                  Dispersion / paste / printed layer (optional)
                </summary>
                <table className="mt-2 w-full">
                  <tbody>
                    {LIQUID_ROWS.map((key) => {
                      const meta = byKey.get(key)
                      return meta ? (
                        <SpecRow
                          key={key}
                          specKey={key}
                          meta={meta}
                          method={template?.methods?.[key]}
                          value={draft.spec[key] ?? ''}
                          sheetValue={templateDraft?.spec[key]}
                          onChange={(value) => setSpec(key, value)}
                        />
                      ) : null
                    })}
                  </tbody>
                </table>
                <Field label="Solvent">
                  <Input value={draft.solvent} onChange={(e) => update({ solvent: e.target.value })} />
                </Field>
              </details>

              <div className="flex items-center justify-between gap-3 pt-1">
                <span className="text-[12px] text-ink-muted">
                  {previewing ? <Spinner label="Re-ranking…" /> : `${entered} parameter${entered === 1 ? '' : 's'} scored live`}
                </span>
                <div className="flex gap-2">
                  <Button type="button" variant="ghost" size="sm" onClick={() => update(EMPTY_DRAFT)}>
                    Clear
                  </Button>
                  <Button type="button" variant="primary" size="sm" onClick={save} disabled={saving || entered === 0 || savedId !== null}>
                    {saving ? 'Saving…' : savedId ? 'Saved' : 'Save this match'}
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          {history && history.length > 0 && (
            <Card title="Saved matches" subtitle="Snapshots of edited sheets, per account">
              <ul className="divide-y divide-[color:var(--border)]">
                {history.slice(0, 8).map((item) => (
                  <li key={item.id} className="flex items-center justify-between gap-2 py-2">
                    <button
                      type="button"
                      onClick={() => loadRecord(item)}
                      className={`min-w-0 flex-1 text-left ${savedId === item.id ? 'text-ink' : 'text-ink-secondary hover:text-ink'}`}
                    >
                      <span className="block truncate text-[13px] font-medium">{item.input.name || 'Untitled material'}</span>
                      <span className="block text-[12px] text-ink-muted">
                        {dateLabel(item.created_at)} · top: {item.report.applications[0]?.name ?? '—'}
                      </span>
                    </button>
                    <Button size="sm" variant="ghost" onClick={() => remove(item)} aria-label="Delete match">
                      ×
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <div className="min-w-0 space-y-4">
          {tab === 'market' ? (
            <MarketDatabase profiles={profiles} />
          ) : report ? (
            <Results report={report} baseline={differsFromSheet ? baseline : null} sheetName={template?.name ?? null} />
          ) : (
            <Card title="Application matches">
              <Empty title="Nothing to rank yet">Enter at least one value from your TDS; the ranking updates as you type.</Empty>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Results
// --------------------------------------------------------------------------- //
function Results({
  report,
  baseline,
  sheetName,
}: {
  report: TdsMatchReport
  baseline: TdsMatchReport | null
  sheetName: string | null
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const evaluated = report.applications.filter((a) => a.verdict !== 'unknown')
  const passing = evaluated.filter((a) => a.verdict === 'pass')
  const baseScores = new Map(baseline?.applications.map((a) => [a.key, a.score]) ?? [])
  const baseRanks = new Map(baseline?.applications.map((a, i) => [a.key, i + 1]) ?? [])

  return (
    <>
      <Card
        title={report.name || 'Application matches'}
        subtitle={`${formLabelOf(report.form)} · ${report.database_size} commercial grades · ${report.market_size} survey grades for percentiles`}
      >
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Applications evaluated" value={String(evaluated.length)} />
          <Stat label="Passing every check" value={String(passing.length)} />
          <Stat
            label="Best fit"
            value={report.applications[0]?.name ?? '—'}
            hint={report.applications[0] ? `score ${report.applications[0].score.toFixed(0)}` : undefined}
          />
          <Stat
            label="Closest grade"
            value={report.closest_products[0] ? `${report.closest_products[0].company} ${report.closest_products[0].product}` : '—'}
            hint={report.closest_products[0] ? `${report.closest_products[0].similarity_pct.toFixed(0)}% similar` : undefined}
          />
        </dl>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {Object.entries(report.entered).map(([key, value]) => (
            <Tag key={key}>
              {labelFor(key, report)} <span className="ml-1 text-ink-muted">{value.text}</span>
            </Tag>
          ))}
        </div>

        {baseline && (
          <p className="mt-3 text-[12px] text-ink-muted">
            Values differ from {sheetName ?? 'your sheet'}; arrows on each application show the change in score and rank
            against the sheet as filed.
          </p>
        )}

        {report.warnings.length > 0 && (
          <div className="mt-3">
            <Banner kind="warning" title="Notes">
              <ul className="list-disc space-y-0.5 pl-4">
                {report.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Banner>
          </div>
        )}
      </Card>

      <Card
        title="Ranked applications"
        subtitle="How your values sit in the range the market sells for each use, blended with the closest grade sold for it"
      >
        <ol className="space-y-2.5">
          {report.applications.map((fit, index) => (
            <ApplicationRow
              key={fit.key}
              fit={fit}
              rank={index + 1}
              scoreDelta={baseScores.has(fit.key) ? fit.score - (baseScores.get(fit.key) ?? 0) : null}
              rankDelta={baseRanks.has(fit.key) ? (baseRanks.get(fit.key) ?? 0) - (index + 1) : null}
              open={expanded === fit.key}
              onToggle={() => setExpanded((current) => (current === fit.key ? null : fit.key))}
            />
          ))}
        </ol>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Closest commercial grades" subtitle="Across every product in the database, any application">
          {report.closest_products.length === 0 ? (
            <Empty title="Not enough shared parameters">Enter at least two of the specs vendors quote.</Empty>
          ) : (
            <ul className="space-y-2.5">
              {report.closest_products.map((p) => (
                <ProductRow key={p.key} product={p} />
              ))}
            </ul>
          )}
        </Card>

        <Card title="Where you sit in the wider market" subtitle="Percentiles against the literature survey of commercial graphene materials">
          {report.market_context.length === 0 ? (
            <Empty title="No market context">The survey does not quote the parameters you entered.</Empty>
          ) : (
            <ul className="space-y-2.5">
              {report.market_context.map((m) => (
                <li key={m.parameter}>
                  <div className="flex items-baseline justify-between gap-3 text-[13px]">
                    <span className="font-medium text-ink">
                      {m.label}{' '}
                      <span className="tabular text-ink-secondary">
                        {num(m.value)} {m.unit}
                      </span>
                    </span>
                    <span className="tabular text-ink-muted">
                      P{m.percentile.toFixed(0)} · n={m.n_products}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full rounded-full bg-[color:var(--page)]">
                    <div className="h-1.5 rounded-full" style={{ width: `${Math.max(2, m.percentile)}%`, background: 'var(--series-1)' }} />
                  </div>
                  <p className="mt-0.5 text-[12px] text-ink-muted">{m.comment}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}

function labelFor(key: string, report: TdsMatchReport): string {
  for (const fit of report.applications) {
    const check = fit.checks.find((c) => c.parameter === key)
    if (check) return check.label
  }
  return key.replace(/_/g, ' ')
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[12px] text-ink-muted">{label}</dt>
      <dd className="mt-0.5 truncate text-[15px] font-semibold text-ink" title={value}>
        {value}
      </dd>
      {hint && <p className="text-[11px] text-ink-muted">{hint}</p>}
    </div>
  )
}

function Delta({ score, rank }: { score: number | null; rank: number | null }) {
  if (score === null && rank === null) return null
  const parts: string[] = []
  if (score !== null && Math.abs(score) >= 0.5) parts.push(`${score > 0 ? '▲' : '▼'} ${Math.abs(score).toFixed(0)} pts`)
  if (rank !== null && rank !== 0) parts.push(`${rank > 0 ? '▲' : '▼'} ${Math.abs(rank)} rank${Math.abs(rank) === 1 ? '' : 's'}`)
  if (parts.length === 0) return <span className="text-[12px] text-ink-muted">unchanged</span>
  const up = (score ?? 0) > 0 || (rank ?? 0) > 0
  return (
    <span className="tabular text-[12px]" style={{ color: up ? 'var(--success-text)' : 'var(--status-critical)' }}>
      {parts.join(' · ')}
    </span>
  )
}

function ApplicationRow({
  fit,
  rank,
  scoreDelta,
  rankDelta,
  open,
  onToggle,
}: {
  fit: ApplicationFit
  rank: number
  scoreDelta: number | null
  rankDelta: number | null
  open: boolean
  onToggle: () => void
}) {
  const colour =
    fit.verdict === 'pass'
      ? 'var(--status-good)'
      : fit.verdict === 'borderline'
        ? 'var(--status-warning)'
        : fit.verdict === 'fail'
          ? 'var(--status-critical)'
          : 'var(--text-muted)'
  return (
    <li className="rounded-lg border border-[color:var(--border)]">
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 p-3.5 text-left">
        <span className="tabular flex size-6 shrink-0 items-center justify-center rounded-md bg-[color:var(--page)] text-[13px] font-semibold text-ink-secondary">
          {rank}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-ink">{fit.name}</h3>
            <div className="flex items-center gap-2">
              <Delta score={scoreDelta} rank={rankDelta} />
              <span className="text-[12px] text-ink-muted">{CONFIDENCE_LABEL[fit.confidence]}</span>
              <span className="tabular text-[13px] text-ink-secondary">score {fit.score.toFixed(0)}</span>
              <VerdictBadge verdict={fit.verdict} />
            </div>
          </div>
          <div className="mt-1.5 h-1.5 w-full rounded-full bg-[color:var(--page)]">
            <div className="h-1.5 rounded-full" style={{ width: `${Math.max(2, fit.score)}%`, background: colour }} />
          </div>
          <p className="mt-1.5 text-[13px] text-ink-secondary">{fit.rationale}</p>
        </div>
      </button>

      {open && (
        <div className="space-y-3 border-t border-[color:var(--border)] px-3.5 py-3">
          <p className="text-[13px] text-ink-secondary">{fit.description}</p>
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-ink-muted">
                  <th className="py-1 pr-3 font-medium">Parameter</th>
                  <th className="py-1 pr-3 font-medium">Yours</th>
                  <th className="py-1 pr-3 font-medium">Market for this use</th>
                  <th className="py-1 pr-3 font-medium">Weight</th>
                  <th className="py-1 font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {fit.checks.map((check) => (
                  <tr key={check.parameter} className="border-t border-[color:var(--border)]">
                    <td className="py-1.5 pr-3 font-medium text-ink">{check.label}</td>
                    <td className="tabular py-1.5 pr-3 text-ink">{check.entered}</td>
                    <td className="tabular py-1.5 pr-3 text-ink-secondary" title={check.comment}>
                      {check.market_range}
                      {check.n_products > 0 && <span className="text-ink-muted"> · {check.n_products} grades</span>}
                    </td>
                    <td className="tabular py-1.5 pr-3 text-ink-muted">{check.weight.toFixed(1)}</td>
                    <td className="py-1.5">
                      <VerdictBadge verdict={check.verdict} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {fit.gaps.length > 0 && (
            <Banner kind="warning" title="Gaps">
              <ul className="list-disc space-y-0.5 pl-4">
                {fit.gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </Banner>
          )}
          {fit.closest_products.length > 0 && (
            <div>
              <p className="mb-1.5 text-[12px] font-medium uppercase tracking-wide text-ink-muted">
                Grades sold for this use that read most like yours
              </p>
              <ul className="space-y-2">
                {fit.closest_products.map((p) => (
                  <ProductRow key={p.key} product={p} compact />
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function ProductRow({ product, compact = false }: { product: ProductSimilarity; compact?: boolean }) {
  return (
    <li className="rounded-lg border border-[color:var(--border)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">
            {product.company} <span className="font-normal text-ink-secondary">{product.product}</span>
          </p>
          <p className="text-[12px] text-ink-muted">
            {formLabelOf(product.form)} · compared on {product.shared_parameters.length} parameter
            {product.shared_parameters.length === 1 ? '' : 's'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="tabular text-[13px] font-semibold text-ink">{product.similarity_pct.toFixed(0)}% similar</span>
          {product.datasheet_file ? (
            <a href={datasheetUrl(product.datasheet_file)} target="_blank" rel="noreferrer" className="text-[12px] text-[color:var(--series-1)] hover:underline">
              TDS
            </a>
          ) : (
            product.datasheet_url && (
              <a href={product.datasheet_url} target="_blank" rel="noreferrer" className="text-[12px] text-[color:var(--series-1)] hover:underline">
                Vendor
              </a>
            )
          )}
        </div>
      </div>
      {!compact && product.applications_text && (
        <p className="mt-1 text-[12px] text-ink-secondary">Sold for: {product.applications_text}</p>
      )}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {product.deltas.map((d) => (
          <Tag key={d.parameter}>
            <span
              aria-hidden="true"
              className="mr-1 inline-block size-1.5 rounded-full"
              style={{ background: d.relation === 'within' ? 'var(--status-good)' : 'var(--status-warning)' }}
            />
            {d.label}: {d.yours} <span className="text-ink-muted">vs {d.theirs}</span>
          </Tag>
        ))}
      </div>
    </li>
  )
}

// --------------------------------------------------------------------------- //
// Market database browser
// --------------------------------------------------------------------------- //
function MarketDatabase({ profiles }: { profiles: ApplicationProfile[] }) {
  const [selected, setSelected] = useState<string>(profiles[0]?.key ?? '')
  const [products, setProducts] = useState<Awaited<ReturnType<typeof api.tdsProducts>> | null>(null)
  const profile = profiles.find((p) => p.key === selected)

  useEffect(() => {
    if (!selected) return
    setProducts(null)
    api.tdsProducts(selected).then(setProducts).catch(() => setProducts([]))
  }, [selected])

  return (
    <>
      <Card
        title="What the market sells into each application"
        subtitle="Envelopes are the union of the ranges quoted by every grade tagged with the application"
      >
        <div className="flex flex-wrap gap-1.5">
          {profiles
            .slice()
            .sort((a, b) => b.evidence_count - a.evidence_count)
            .map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setSelected(p.key)}
                className={`rounded-md border px-2.5 py-1 text-[13px] transition-colors ${
                  p.key === selected
                    ? 'border-[color:var(--series-1)] bg-[color:var(--page)] text-ink'
                    : 'border-[color:var(--border)] text-ink-secondary hover:text-ink'
                }`}
              >
                {p.name} <span className="tabular text-ink-muted">{p.evidence_count}</span>
              </button>
            ))}
        </div>

        {profile && (
          <div className="mt-4 space-y-3">
            <p className="text-[13px] text-ink-secondary">{profile.description}</p>
            <p className="text-[12px] text-ink-muted">
              {profile.evidence_count} grades from {profile.companies.join(', ') || 'no vendor yet'}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[12px] text-ink-muted">
                    <th className="py-1 pr-3 font-medium">Parameter</th>
                    <th className="py-1 pr-3 font-medium">Market range</th>
                    <th className="py-1 pr-3 font-medium">Typical</th>
                    <th className="py-1 pr-3 font-medium">Grades quoting it</th>
                    <th className="py-1 font-medium">Weight</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(profile.parameter_weights)
                    .sort((a, b) => b[1] - a[1])
                    .map(([key, weight]) => {
                      const env = profile.envelopes[key]
                      return (
                        <tr key={key} className="border-t border-[color:var(--border)]">
                          <td className="py-1.5 pr-3 font-medium text-ink">{key.replace(/_/g, ' ')}</td>
                          <td className="tabular py-1.5 pr-3 text-ink-secondary">{env ? env.text : 'not quoted'}</td>
                          <td className="tabular py-1.5 pr-3 text-ink-secondary">{env?.typical != null ? num(env.typical) : '—'}</td>
                          <td className="tabular py-1.5 pr-3 text-ink-muted">{env ? env.n : 0}</td>
                          <td className="tabular py-1.5 text-ink-muted">{weight.toFixed(1)}</td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      <Card title="Grades sold into this application" subtitle="From the vendor survey and datasheets">
        {products === null ? (
          <Spinner label="Loading grades…" />
        ) : products.length === 0 ? (
          <Empty title="No grades yet">No product in the database is tagged with this application.</Empty>
        ) : (
          <ul className="divide-y divide-[color:var(--border)]">
            {products.map((p) => (
              <li key={p.key} className="py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-[13px] font-semibold text-ink">
                    {p.company} <span className="font-normal text-ink-secondary">{p.product}</span>
                    <span className="ml-2 text-[12px] font-normal text-ink-muted">{formLabelOf(p.form)}</span>
                  </p>
                  <div className="flex items-center gap-2 text-[12px]">
                    {p.datasheet_file && (
                      <a href={datasheetUrl(p.datasheet_file)} target="_blank" rel="noreferrer" className="text-[color:var(--series-1)] hover:underline">
                        TDS PDF
                      </a>
                    )}
                    {p.datasheet_url && (
                      <a href={p.datasheet_url} target="_blank" rel="noreferrer" className="text-[color:var(--series-1)] hover:underline">
                        Vendor page
                      </a>
                    )}
                  </div>
                </div>
                {p.applications_text && <p className="mt-0.5 text-[12px] text-ink-secondary">Sold for: {p.applications_text}</p>}
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {Object.entries(p.specs).map(([key, value]) => (
                    <Tag key={key}>
                      {key.replace(/_/g, ' ')} <span className="ml-1 text-ink-muted">{value.text}</span>
                    </Tag>
                  ))}
                </div>
                {p.corrections.length > 0 && (
                  <p className="mt-1 text-[11px] text-ink-muted">
                    {p.corrections.length} value{p.corrections.length === 1 ? '' : 's'} taken from the datasheet where the survey sheet disagreed.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}

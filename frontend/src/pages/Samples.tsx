import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { dateLabel, num, routeLabel } from '../lib/format'
import { PRODUCTION_ROUTES, type Sample } from '../lib/types'
import { Banner, Button, Card, Empty, Field, Input, Select, Spinner, Tag } from '../components/ui'

export function Samples() {
  const [samples, setSamples] = useState<Sample[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const [name, setName] = useState('')
  const [route, setRoute] = useState('unknown')
  const [feedstock, setFeedstock] = useState('')
  const [batchId, setBatchId] = useState('')
  const [bet, setBet] = useState('')
  const [flake, setFlake] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .listSamples()
      .then(setSamples)
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
  }, [])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const created = await api.createSample({
        name,
        production_route: route,
        feedstock: feedstock || null,
        batch_id: batchId || null,
        properties: {
          bet_m2_g: bet ? Number(bet) : null,
          flake_size_um: flake ? Number(flake) : null,
        },
      })
      setSamples((current) => [created, ...(current ?? [])])
      setName('')
      setFeedstock('')
      setBatchId('')
      setBet('')
      setFlake('')
      setCreating(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold text-ink">Samples</h1>
          <p className="text-[13px] text-ink-secondary">
            Each sample holds its spectra, properties and analysis history.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating((value) => !value)}>
          {creating ? 'Cancel' : 'New sample'}
        </Button>
      </div>

      {error && <Banner kind="error">{error}</Banner>}

      {creating && (
        <Card title="New sample">
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-2">
            <Field label="Sample name">
              <Input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. LPE batch 2026-08-A"
              />
            </Field>
            <Field label="Production route">
              <Select value={route} onChange={(event) => setRoute(event.target.value)}>
                {PRODUCTION_ROUTES.map((option) => (
                  <option key={option} value={option}>
                    {routeLabel(option)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Feedstock (optional)">
              <Input
                value={feedstock}
                onChange={(event) => setFeedstock(event.target.value)}
                placeholder="e.g. flake graphite"
              />
            </Field>
            <Field label="Batch ID (optional)">
              <Input value={batchId} onChange={(event) => setBatchId(event.target.value)} />
            </Field>
            <Field
              label="BET surface area (m²/g, optional)"
              hint="Used by the application matcher and peer distance."
            >
              <Input
                type="number"
                step="any"
                min="0"
                value={bet}
                onChange={(event) => setBet(event.target.value)}
              />
            </Field>
            <Field label="Flake size D50 (µm, optional)">
              <Input
                type="number"
                step="any"
                min="0"
                value={flake}
                onChange={(event) => setFlake(event.target.value)}
              />
            </Field>
            <div className="sm:col-span-2">
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? <Spinner /> : 'Create sample'}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {samples === null && !error && (
        <Card>
          <Spinner label="Loading samples…" />
        </Card>
      )}

      {samples?.length === 0 && (
        <Empty title="No samples yet">
          Create a sample, then upload a Raman or XPS export to benchmark it.
        </Empty>
      )}

      {samples && samples.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {samples.map((sample) => (
            <Link
              key={sample.id}
              to={`/samples/${sample.id}`}
              className="block rounded-xl border border-[color:var(--border)] bg-surface p-4 transition-colors hover:border-[color:var(--series-1)]"
            >
              <h2 className="truncate text-sm font-semibold text-ink">{sample.name}</h2>
              <p className="mt-0.5 text-[12px] text-ink-muted">{dateLabel(sample.created_at)}</p>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                <Tag>{routeLabel(sample.production_route)}</Tag>
                {sample.batch_id && <Tag>Batch {sample.batch_id}</Tag>}
                {sample.properties.bet_m2_g != null && (
                  <Tag>BET {num(sample.properties.bet_m2_g)} m²/g</Tag>
                )}
                {sample.properties.flake_size_um != null && (
                  <Tag>D50 {num(sample.properties.flake_size_um)} µm</Tag>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

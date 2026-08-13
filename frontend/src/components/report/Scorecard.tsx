import { Card, Empty, VerdictBadge } from '../ui'
import { num, VERDICT_META } from '../../lib/format'
import type { ScorecardRow } from '../../lib/types'

export function Scorecard({ rows }: { rows: ScorecardRow[] }) {
  if (rows.length === 0) {
    return (
      <Card title="Metric scorecard">
        <Empty title="No metrics available">
          Upload a Raman or XPS spectrum and run the analysis to populate the scorecard.
        </Empty>
      </Card>
    )
  }

  const assessed = rows.filter((row) => row.verdict !== 'unknown')
  const informational = rows.filter((row) => row.verdict === 'unknown')

  return (
    <Card
      title="Metric scorecard"
      subtitle="Measured values against the quality targets for the classified form"
    >
      <div className="grid gap-2.5 sm:grid-cols-2">
        {assessed.map((row) => (
          <Tile key={row.metric} row={row} />
        ))}
      </div>

      {informational.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-[12px] font-medium text-ink-muted">
            Measured, no target defined for this form
          </p>
          <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-3">
            {informational.map((row) => (
              <div key={row.metric} className="min-w-0">
                <dt className="truncate text-[12px] text-ink-muted">{row.label}</dt>
                <dd className="tabular text-[14px] font-medium text-ink">
                  {num(row.value)}
                  {row.unit && (
                    <span className="ml-1 text-[11px] font-normal text-ink-secondary">{row.unit}</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </Card>
  )
}

function Tile({ row }: { row: ScorecardRow }) {
  const meta = VERDICT_META[row.verdict]
  return (
    <div
      className="rounded-lg border p-3"
      style={{
        borderColor: `color-mix(in srgb, ${meta.color} 35%, transparent)`,
        background: `color-mix(in srgb, ${meta.color} 6%, transparent)`,
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12px] text-ink-secondary">{row.label}</p>
          <p className="tabular mt-0.5 text-[20px] leading-tight font-semibold text-ink">
            {num(row.value)}
            {row.unit && (
              <span className="ml-1 text-[12px] font-normal text-ink-secondary">{row.unit}</span>
            )}
          </p>
        </div>
        <VerdictBadge verdict={row.verdict} />
      </div>
      {row.target_range && (
        <p className="tabular mt-1.5 text-[12px] text-ink-muted">Target {row.target_range}</p>
      )}
      <p className="mt-1.5 text-[12px] text-ink-secondary">{row.comment}</p>
    </div>
  )
}

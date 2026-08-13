import { Banner, Card, Empty, Tag, VerdictBadge } from '../ui'
import { num } from '../../lib/format'
import type { ApplicationMatch } from '../../lib/types'

export function ApplicationPanel({ matches }: { matches: ApplicationMatch[] }) {
  if (matches.length === 0) {
    return (
      <Card title="Recommended applications">
        <Empty title="No application match yet">Run an analysis to see the best-fit applications.</Empty>
      </Card>
    )
  }

  return (
    <Card
      title="Recommended applications"
      subtitle="Top three matches, scored on the measured criteria and the classified form"
    >
      <ol className="space-y-3">
        {matches.map((match, index) => (
          <li
            key={match.key}
            className="rounded-lg border border-[color:var(--border)] p-3.5"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <span className="tabular flex size-6 items-center justify-center rounded-md bg-[color:var(--page)] text-[13px] font-semibold text-ink-secondary">
                  {index + 1}
                </span>
                <h3 className="text-sm font-semibold text-ink">{match.name}</h3>
              </div>
              <div className="flex items-center gap-2">
                <span className="tabular text-[13px] text-ink-secondary">
                  score {match.score.toFixed(0)}
                </span>
                <VerdictBadge verdict={match.verdict} />
              </div>
            </div>

            <p className="mt-2 text-[13px] text-ink-secondary">{match.rationale}</p>

            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {match.checks.map((check) => (
                <Tag key={check.metric} className="gap-1">
                  <span
                    aria-hidden="true"
                    className="mr-1 inline-block size-1.5 rounded-full"
                    style={{
                      background:
                        check.verdict === 'pass'
                          ? 'var(--status-good)'
                          : check.verdict === 'borderline'
                            ? 'var(--status-warning)'
                            : check.verdict === 'fail'
                              ? 'var(--status-critical)'
                              : 'var(--text-muted)',
                    }}
                  />
                  <span title={check.comment}>
                    {check.label} {check.measured === null ? '—' : num(Number(check.measured))}
                    <span className="text-ink-muted"> / {check.target}</span>
                  </span>
                </Tag>
              ))}
            </div>

            {match.mismatches.length > 0 && (
              <div className="mt-2.5">
                <Banner kind="warning" title="Mismatch">
                  <ul className="list-disc space-y-0.5 pl-4">
                    {match.mismatches.map((mismatch) => (
                      <li key={mismatch}>{mismatch}</li>
                    ))}
                  </ul>
                </Banner>
              </div>
            )}
          </li>
        ))}
      </ol>
    </Card>
  )
}

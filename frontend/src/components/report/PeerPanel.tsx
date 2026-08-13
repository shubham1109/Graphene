import { Card, Empty, Tag } from '../ui'
import { formLabel, num, signedPct } from '../../lib/format'
import type { PeerMatch } from '../../lib/types'

export function PeerPanel({ peers }: { peers: PeerMatch[] }) {
  if (peers.length === 0) {
    return (
      <Card title="Closest commercial products">
        <Empty title="No peer match available">
          Peer matching needs at least two comparable specifications. Add BET surface area or flake
          size to the sample, or upload XPS data, to widen the comparison.
        </Empty>
      </Card>
    )
  }

  return (
    <Card
      title="Closest commercial products"
      subtitle="Nearest public datasheet specifications by weighted spec distance"
    >
      <ol className="space-y-3">
        {peers.map((peer, index) => (
          <li key={peer.product_id} className="rounded-lg border border-[color:var(--border)] p-3.5">
            {/* No flex-wrap: a long product name must wrap inside its own
                column rather than pushing the similarity figure onto a new
                line, where its right-alignment reads as left-aligned. */}
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2.5">
                  <span className="tabular flex size-6 items-center justify-center rounded-md bg-[color:var(--page)] text-[13px] font-semibold text-ink-secondary">
                    {index + 1}
                  </span>
                  <h3 className="min-w-0 text-sm font-semibold text-ink">
                    {peer.producer} <span className="font-normal text-ink-secondary">·</span>{' '}
                    {peer.product_name}
                  </h3>
                </div>
                <div className="mt-1.5 ml-8.5 flex flex-wrap gap-1.5">
                  <Tag>{formLabel(peer.form)}</Tag>
                  {peer.region && <Tag>{peer.region}</Tag>}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <p className="tabular text-[17px] leading-tight font-semibold text-ink">
                  {peer.similarity_pct.toFixed(0)}%
                </p>
                <p className="text-[11px] text-ink-muted">similarity</p>
              </div>
            </div>

            <p className="mt-2 text-[13px] text-ink-secondary">{peer.summary}</p>

            <table className="mt-2.5 w-full text-[12px]">
              <thead>
                <tr className="border-b border-[color:var(--border)] text-left text-ink-muted">
                  <th className="py-1 pr-3 font-medium">Specification</th>
                  <th className="py-1 pr-3 font-medium">Sample</th>
                  <th className="py-1 pr-3 font-medium">Product</th>
                  <th className="py-1 font-medium">Difference</th>
                </tr>
              </thead>
              <tbody>
                {peer.deltas.map((delta) => (
                  <tr key={delta.feature} className="border-b border-[color:var(--border)] last:border-0">
                    <td className="py-1 pr-3 text-ink-secondary">{delta.label}</td>
                    <td className="tabular py-1 pr-3 text-ink">{num(delta.sample_value)}</td>
                    <td className="tabular py-1 pr-3 text-ink-secondary">{num(delta.product_value)}</td>
                    <td className="tabular py-1 text-ink-secondary">
                      {signedPct(delta.percent_difference)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {peer.datasheet_url && (
              <a
                href={peer.datasheet_url}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-2.5 inline-block text-[13px] font-medium text-[color:var(--series-1)] underline decoration-1 underline-offset-2"
              >
                Producer datasheet ↗
              </a>
            )}
          </li>
        ))}
      </ol>

      <p className="mt-3 text-[12px] text-ink-muted">
        Producer specifications are nominal grade values compiled from public datasheets, not batch
        certificates. Confirm current-batch specifications with the vendor before purchasing.
      </p>
    </Card>
  )
}

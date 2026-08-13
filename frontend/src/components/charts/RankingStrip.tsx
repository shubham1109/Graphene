import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-basic-dist-min'
import { num } from '../../lib/format'
import type { GlobalRanking } from '../../lib/types'
import { useTokens } from './useTokens'

/** Where the sample sits among reference materials, one metric per row.
 *
 * A dot plot rather than bars: several of these metrics (FWHM, band positions)
 * have no meaningful zero, and bar length would imply a magnitude-from-zero
 * reading that is simply wrong for them. Position on a shared line is the
 * honest encoding for "where do I sit in this population". */
export function RankingStrip({ rankings, height = 300 }: { rankings: GlobalRanking[]; height?: number }) {
  const node = useRef<HTMLDivElement>(null)
  const tokens = useTokens()

  useEffect(() => {
    const element = node.current
    if (!element || rankings.length === 0) return

    const rows = [...rankings].reverse()
    const labels = rows.map((row) => row.label)

    // Each metric has its own units, so every row is scaled to its own span.
    // The axis is therefore "position within the reference range", 0-1.
    const scaled = rows.map((row) => {
      const values = [...row.reference_values, row.sample_value]
      const min = Math.min(...values)
      const max = Math.max(...values)
      const span = max - min || 1
      return {
        row,
        references: row.reference_values.map((value) => (value - min) / span),
        sample: (row.sample_value - min) / span,
      }
    })

    const data: Plotly.Data[] = [
      {
        type: 'scatter',
        mode: 'markers',
        name: 'Reference materials',
        x: scaled.flatMap((entry) => entry.references),
        y: scaled.flatMap((entry) => entry.references.map(() => entry.row.label)),
        text: scaled.flatMap((entry) =>
          entry.references.map(
            (_value, index) =>
              `${entry.row.reference_labels[index]}: ${num(entry.row.reference_values[index])}`,
          ),
        ),
        marker: {
          size: 9,
          color: tokens.muted,
          opacity: 0.75,
          line: { color: tokens.surface, width: 2 },
        },
        hovertemplate: '%{text}<extra></extra>',
      },
      {
        type: 'scatter',
        mode: 'text+markers',
        name: 'This sample',
        x: scaled.map((entry) => entry.sample),
        y: labels,
        text: scaled.map((entry) => num(entry.row.sample_value)),
        textposition: 'top center',
        textfont: { size: 11, color: tokens.ink },
        marker: {
          size: 15,
          color: tokens.series[0],
          symbol: 'diamond',
          line: { color: tokens.surface, width: 2 },
        },
        customdata: scaled.map((entry) => [
          entry.row.percentile.toFixed(0),
          entry.row.n_references,
          entry.row.better_is_lower ? 'lower is better' : 'higher is better',
        ]),
        hovertemplate:
          '<b>This sample: %{text}</b><br>%{customdata[0]}th percentile of %{customdata[1]} references<br>(%{customdata[2]})<extra></extra>',
      },
    ]

    const layout: Partial<Plotly.Layout> = {
      height,
      margin: { l: 150, r: 24, t: 26, b: 40 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: tokens.inkSecondary },
      hovermode: 'closest',
      hoverlabel: {
        bgcolor: tokens.surface,
        bordercolor: tokens.baseline,
        font: { color: tokens.ink, size: 12 },
      },
      xaxis: {
        title: { text: 'Position within the reference range', font: { size: 11, color: tokens.muted } },
        // Padded either side so a 15px marker sitting at 0 or 1 is not clipped
        // by the plot boundary, and the direct label above it stays inside.
        range: [-0.13, 1.18],
        showgrid: false,
        zeroline: false,
        showticklabels: false,
        linecolor: tokens.baseline,
      },
      yaxis: {
        type: 'category',
        automargin: true,
        showgrid: true,
        gridcolor: tokens.gridline,
        zeroline: false,
        linecolor: tokens.baseline,
        tickfont: { size: 12, color: tokens.inkSecondary },
      },
      showlegend: true,
      legend: {
        orientation: 'h',
        y: 1.18,
        x: 0,
        font: { size: 12, color: tokens.inkSecondary },
        bgcolor: 'rgba(0,0,0,0)',
      },
    }

    Plotly.react(element, data, layout, {
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'zoom2d', 'pan2d'],
    })
  }, [rankings, height, tokens])

  useEffect(() => {
    const element = node.current
    return () => {
      if (element) Plotly.purge(element)
    }
  }, [])

  if (rankings.length === 0) return null
  return (
    <div>
      <div ref={node} className="w-full" role="img" aria-label="Sample position within the reference library" />
      {/* Table view: the relief for sub-3:1 marks and the accessible fallback. */}
      <details className="mt-3">
        <summary className="cursor-pointer text-[13px] text-ink-secondary">
          View ranking as a table
        </summary>
        <table className="mt-2 w-full text-[13px]">
          <thead>
            <tr className="border-b border-[color:var(--border)] text-left text-ink-muted">
              <th className="py-1.5 pr-3 font-medium">Metric</th>
              <th className="py-1.5 pr-3 font-medium">Sample</th>
              <th className="py-1.5 pr-3 font-medium">Percentile</th>
              <th className="py-1.5 font-medium">References</th>
            </tr>
          </thead>
          <tbody>
            {rankings.map((row) => (
              <tr key={row.metric} className="border-b border-[color:var(--border)]">
                <td className="py-1.5 pr-3 text-ink">{row.label}</td>
                <td className="tabular py-1.5 pr-3 text-ink">{num(row.sample_value)}</td>
                <td className="tabular py-1.5 pr-3 text-ink-secondary">
                  {row.percentile.toFixed(0)}th
                </td>
                <td className="tabular py-1.5 text-ink-muted">{row.n_references}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-basic-dist-min'
import { useTokens } from './useTokens'

export interface Trace {
  name: string
  x: number[]
  y: number[]
  /** The measured sample is emphasised; references are supporting context. */
  emphasis?: boolean
  dashed?: boolean
}

interface Props {
  traces: Trace[]
  xTitle: string
  yTitle: string
  /** XPS binding-energy axes are conventionally drawn high-to-low. */
  reverseX?: boolean
  height?: number
  /** Peak positions to annotate on the emphasised trace. */
  markers?: { x: number; label: string }[]
}

/** Scale every trace to its own maximum so shapes are comparable.
 *
 * Reference traces are synthesised from published peak parameters and carry no
 * absolute intensity, and Raman counts depend on laser power and integration
 * time, so raw intensities are not comparable between any two traces. Band
 * ratios and positions - what the analysis actually uses - are preserved. */
function normalise(y: number[]): number[] {
  let max = 0
  for (const value of y) if (Number.isFinite(value) && value > max) max = value
  if (max <= 0) return y.map(() => 0)
  return y.map((value) => (Number.isFinite(value) ? value / max : 0))
}

export function SpectrumPlot({
  traces,
  xTitle,
  yTitle,
  reverseX = false,
  height = 380,
  markers = [],
}: Props) {
  const node = useRef<HTMLDivElement>(null)
  const tokens = useTokens()

  useEffect(() => {
    const element = node.current
    if (!element) return

    // Categorical hues are assigned in fixed slot order, never cycled: the
    // emphasised sample always takes slot 1 and references take slots 2, 3, 4
    // in the order they appear. Deriving the slot from the trace's overall
    // index would skip slot 2, and the slot ordering is what makes the palette
    // colourblind-safe for adjacent pairs.
    let referenceSlot = 0
    const data: Plotly.Data[] = traces.map((trace) => {
      const colour = trace.emphasis
        ? tokens.series[0]
        : tokens.series[1 + (referenceSlot++ % (tokens.series.length - 1))]
      return {
        type: 'scatter',
        mode: 'lines',
        name: trace.name,
        x: trace.x,
        y: normalise(trace.y),
        line: {
          color: colour,
          width: trace.emphasis ? 2.5 : 2,
          dash: trace.dashed ? 'dot' : 'solid',
          shape: 'linear',
        },
        opacity: trace.emphasis ? 1 : 0.85,
        hovertemplate: `<b>${trace.name}</b><br>%{x:.4g} · %{y:.3f}<extra></extra>`,
      }
    })

    const layout: Partial<Plotly.Layout> = {
      height,
      margin: { l: 54, r: 16, t: 12, b: 46 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: tokens.inkSecondary },
      // Crosshair + shared tooltip: the default interaction for line charts.
      hovermode: 'x unified',
      hoverlabel: {
        bgcolor: tokens.surface,
        bordercolor: tokens.baseline,
        font: { color: tokens.ink, size: 12 },
      },
      xaxis: {
        title: { text: xTitle, font: { size: 12, color: tokens.muted } },
        autorange: reverseX ? 'reversed' : true,
        gridcolor: tokens.gridline,
        zeroline: false,
        linecolor: tokens.baseline,
        tickcolor: tokens.baseline,
        tickfont: { color: tokens.muted, size: 11 },
        showspikes: true,
        spikemode: 'across',
        spikethickness: 1,
        spikedash: 'dot',
        spikecolor: tokens.baseline,
      },
      yaxis: {
        title: { text: yTitle, font: { size: 12, color: tokens.muted } },
        gridcolor: tokens.gridline,
        zeroline: false,
        linecolor: tokens.baseline,
        tickfont: { color: tokens.muted, size: 11 },
        rangemode: 'tozero',
      },
      showlegend: traces.length > 1,
      legend: {
        orientation: 'h',
        y: -0.22,
        x: 0,
        font: { size: 12, color: tokens.inkSecondary },
        bgcolor: 'rgba(0,0,0,0)',
      },
      annotations: markers.map((marker) => ({
        x: marker.x,
        y: 1.02,
        yref: 'paper' as const,
        text: marker.label,
        showarrow: false,
        font: { size: 10, color: tokens.muted },
      })),
      shapes: markers.map((marker) => ({
        type: 'line' as const,
        x0: marker.x,
        x1: marker.x,
        y0: 0,
        y1: 1,
        yref: 'paper' as const,
        line: { color: tokens.gridline, width: 1, dash: 'dot' as const },
      })),
    }

    Plotly.react(element, data, layout, {
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    })
  }, [traces, xTitle, yTitle, reverseX, height, markers, tokens])

  useEffect(() => {
    const element = node.current
    return () => {
      if (element) Plotly.purge(element)
    }
  }, [])

  return <div ref={node} className="w-full" role="img" aria-label={`${yTitle} against ${xTitle}`} />
}

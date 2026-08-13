import { useEffect, useMemo, useState } from 'react'
import { api } from '../../lib/api'
import { num } from '../../lib/format'
import type { AnalysisReport, ReferenceSpectrum, Spectrum } from '../../lib/types'
import { SpectrumPlot, type Trace } from '../charts/SpectrumPlot'
import { Banner, Button, Card, Empty, Tag } from '../ui'

const MAX_REFERENCES = 3

export function SpectraPanel({
  spectra,
  report,
}: {
  spectra: Spectrum[]
  report: AnalysisReport | null
}) {
  const raman = useMemo(() => spectra.filter((s) => s.technique === 'raman'), [spectra])
  const xps = useMemo(() => spectra.filter((s) => s.technique === 'xps'), [spectra])

  return (
    <div className="space-y-4">
      {raman.length > 0 && <RamanOverlay spectrum={raman[raman.length - 1]} report={report} />}
      {xps.map((spectrum) => (
        <XPSPlot key={spectrum.id} spectrum={spectrum} report={report} />
      ))}
      {spectra.length === 0 && (
        <Card title="Spectra">
          <Empty title="No spectra uploaded">
            Upload a Raman or XPS export to see it plotted against the open reference library.
          </Empty>
        </Card>
      )}
    </div>
  )
}

function RamanOverlay({
  spectrum,
  report,
}: {
  spectrum: Spectrum
  report: AnalysisReport | null
}) {
  const [references, setReferences] = useState<ReferenceSpectrum[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .referenceSpectra('raman')
      .then((records) => {
        setReferences(records)
        // Pre-select the reference that matches the classified form, so the
        // first thing the user sees is the most relevant comparison.
        const form = report?.classification.form
        const match = records.find((record) => record.material_class === form)
        setSelected(match ? [match.key] : records.slice(0, 1).map((r) => r.key))
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
  }, [report?.classification.form])

  const traces: Trace[] = useMemo(() => {
    const own: Trace = {
      name: `This sample (${spectrum.meta.filename})`,
      x: spectrum.preview_x,
      y: spectrum.preview_y,
      emphasis: true,
    }
    const chosen = references
      .filter((record) => selected.includes(record.key))
      .slice(0, MAX_REFERENCES)
      .map<Trace>((record) => ({
        name: record.label,
        x: record.curve_x,
        y: record.curve_y,
        dashed: true,
      }))
    return [own, ...chosen]
  }, [spectrum, references, selected])

  const markers = useMemo(
    () =>
      (report?.raman?.peaks ?? []).map((peak) => ({
        x: peak.center_cm1,
        label: peak.name,
      })),
    [report],
  )

  function toggle(key: string) {
    setSelected((current) => {
      if (current.includes(key)) return current.filter((item) => item !== key)
      // Cap the overlay: past four lines the palette's adjacent-pair guarantee
      // no longer holds and the plot stops being readable anyway.
      if (current.length >= MAX_REFERENCES) return [...current.slice(1), key]
      return [...current, key]
    })
  }

  return (
    <Card
      title="Raman overlay"
      subtitle={`${spectrum.meta.n_points} points · ${num(spectrum.meta.x_min)}–${num(spectrum.meta.x_max)} cm⁻¹${
        spectrum.meta.excitation_nm ? ` · ${spectrum.meta.excitation_nm} nm excitation` : ''
      }`}
    >
      {error && (
        <div className="mb-3">
          <Banner kind="error">{error}</Banner>
        </div>
      )}

      <SpectrumPlot
        traces={traces}
        xTitle="Raman shift (cm⁻¹)"
        yTitle="Normalised intensity"
        markers={markers}
      />

      <div className="mt-3">
        <p className="mb-1.5 text-[12px] font-medium text-ink-muted">
          Compare against reference materials (up to {MAX_REFERENCES})
        </p>
        <div className="flex flex-wrap gap-1.5">
          {references.map((record) => {
            const active = selected.includes(record.key)
            return (
              <Button
                key={record.key}
                size="sm"
                variant={active ? 'primary' : 'secondary'}
                onClick={() => toggle(record.key)}
                aria-pressed={active}
                title={record.provenance.source}
              >
                {record.label}
              </Button>
            )
          })}
        </div>
        <p className="mt-2 text-[12px] text-ink-muted">
          Reference traces are synthesised from published peak positions and widths, not raw dataset
          files. Every trace is scaled to its own maximum, because absolute Raman intensity depends on
          laser power and integration time and is not comparable between measurements.
        </p>
      </div>

      {report?.raman && <RamanMetrics report={report} />}
    </Card>
  )
}

function RamanMetrics({ report }: { report: AnalysisReport }) {
  const raman = report.raman
  if (!raman) return null
  return (
    <div className="mt-4 border-t border-[color:var(--border)] pt-3">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[color:var(--border)] text-left text-ink-muted">
            <th className="py-1 pr-3 font-medium">Band</th>
            <th className="py-1 pr-3 font-medium">Centre (cm⁻¹)</th>
            <th className="py-1 pr-3 font-medium">FWHM (cm⁻¹)</th>
            <th className="py-1 pr-3 font-medium">Height</th>
            <th className="py-1 font-medium">Lineshape</th>
          </tr>
        </thead>
        <tbody>
          {raman.peaks.map((peak) => (
            <tr key={peak.name} className="border-b border-[color:var(--border)] last:border-0">
              <td className="py-1 pr-3 font-medium text-ink">{peak.name}</td>
              <td className="tabular py-1 pr-3 text-ink-secondary">
                {peak.center_cm1.toFixed(1)}
                {peak.center_stderr ? ` ± ${peak.center_stderr.toFixed(1)}` : ''}
              </td>
              <td className="tabular py-1 pr-3 text-ink-secondary">{peak.fwhm_cm1.toFixed(1)}</td>
              <td className="tabular py-1 pr-3 text-ink-secondary">{num(peak.height)}</td>
              <td className="py-1 text-ink-muted">{peak.shape}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Tag>Fit R² {raman.fit_r_squared?.toFixed(4) ?? '—'}</Tag>
        <Tag>Baseline {raman.baseline_method}</Tag>
        {raman.two_d_single_lorentzian !== null && (
          <Tag>2D {raman.two_d_single_lorentzian ? 'single Lorentzian' : 'multi-component'}</Tag>
        )}
        {raman.crystallite_size_la_nm && <Tag>Lₐ {num(raman.crystallite_size_la_nm)} nm</Tag>}
      </div>
      {raman.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[12px] text-ink-muted">
          {raman.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

const REGION_TITLES: Record<string, string> = {
  c1s: 'XPS C 1s',
  o1s: 'XPS O 1s',
  survey: 'XPS survey',
  unknown: 'XPS region',
}

function XPSPlot({ spectrum, report }: { spectrum: Spectrum; report: AnalysisReport | null }) {
  const components =
    spectrum.region === 'c1s'
      ? (report?.xps?.c1s_components ?? [])
      : spectrum.region === 'o1s'
        ? (report?.xps?.o1s_components ?? [])
        : []

  return (
    <Card
      title={REGION_TITLES[spectrum.region] ?? 'XPS'}
      subtitle={`${spectrum.meta.filename} · ${num(spectrum.meta.x_min)}–${num(spectrum.meta.x_max)} eV · ${spectrum.meta.detected_format}`}
    >
      <SpectrumPlot
        traces={[
          {
            name: `This sample (${spectrum.region.toUpperCase()})`,
            x: spectrum.preview_x,
            y: spectrum.preview_y,
            emphasis: true,
          },
        ]}
        xTitle="Binding energy (eV)"
        yTitle="Normalised intensity"
        reverseX
        height={320}
        markers={components.map((component) => ({
          x: component.binding_energy_ev,
          label: component.name,
        }))}
      />

      {components.length > 0 && (
        <table className="mt-3 w-full text-[12px]">
          <thead>
            <tr className="border-b border-[color:var(--border)] text-left text-ink-muted">
              <th className="py-1 pr-3 font-medium">Component</th>
              <th className="py-1 pr-3 font-medium">Assignment</th>
              <th className="py-1 pr-3 font-medium">BE (eV)</th>
              <th className="py-1 pr-3 font-medium">FWHM (eV)</th>
              <th className="py-1 font-medium">Fraction</th>
            </tr>
          </thead>
          <tbody>
            {components.map((component) => (
              <tr key={component.name} className="border-b border-[color:var(--border)] last:border-0">
                <td className="py-1 pr-3 font-medium text-ink">{component.name}</td>
                <td className="py-1 pr-3 text-ink-secondary">{component.assignment}</td>
                <td className="tabular py-1 pr-3 text-ink-secondary">
                  {component.binding_energy_ev.toFixed(2)}
                </td>
                <td className="tabular py-1 pr-3 text-ink-secondary">{component.fwhm_ev.toFixed(2)}</td>
                <td className="tabular py-1 text-ink-secondary">
                  {(component.fraction_of_region * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {spectrum.meta.warnings.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[12px] text-ink-muted">
          {spectrum.meta.warnings.map((warning) => (
            <li key={warning.code}>{warning.message}</li>
          ))}
        </ul>
      )}
    </Card>
  )
}

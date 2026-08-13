import { RankingStrip } from '../charts/RankingStrip'
import { Banner, Card, Metric, Tag, VerdictBadge } from '../ui'
import { dateLabel, formLabel, num, pct, signedPct } from '../../lib/format'
import type { AnalysisReport, Spectrum } from '../../lib/types'
import { ApplicationPanel } from './ApplicationPanel'
import { PeerPanel } from './PeerPanel'
import { Scorecard } from './Scorecard'
import { SpectraPanel } from './SpectraPanel'

export function ReportView({
  report,
  spectra,
}: {
  report: AnalysisReport
  spectra: Spectrum[]
}) {
  return (
    <div className="space-y-4">
      <ClassificationHeader report={report} />

      {report.warnings.length > 0 && (
        <Banner kind="warning" title="Caveats on this analysis">
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
            {report.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Banner>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Scorecard rows={report.scorecard} />
        <div className="space-y-4">
          {report.rankings.length > 0 && (
            <Card
              title="Global ranking"
              subtitle="Where this sample sits among the open reference materials"
            >
              <RankingStrip rankings={report.rankings} height={Math.max(220, report.rankings.length * 52)} />
            </Card>
          )}
          {report.xps && <ChemistryPanel report={report} />}
        </div>
      </div>

      <SpectraPanel spectra={spectra} report={report} />

      <div className="grid gap-4 xl:grid-cols-2">
        <ApplicationPanel matches={report.applications} />
        <PeerPanel peers={report.peers} />
      </div>

      {report.dft && <DFTPanel report={report} />}

      <CitationsPanel report={report} />
    </div>
  )
}

function ClassificationHeader({ report }: { report: AnalysisReport }) {
  const { classification, raman, xps } = report
  const confidence = classification.confidence
  const confidenceLabel = confidence >= 0.75 ? 'High' : confidence >= 0.5 ? 'Moderate' : 'Low'

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[12px] text-ink-muted">Auto-classified form</p>
          <h1 className="mt-0.5 text-[26px] leading-tight font-semibold text-ink">
            {classification.label}
          </h1>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Tag>
              {confidenceLabel} confidence · {pct(confidence * 100)}
            </Tag>
            {classification.layer_estimate && <Tag>Layers {classification.layer_estimate}</Tag>}
            <Tag>Engine v{report.engine_version}</Tag>
            <Tag>{dateLabel(report.created_at)}</Tag>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <Metric label="I(D)/I(G)" value={num(raman?.id_ig)} />
          <Metric label="I(2D)/I(G)" value={num(raman?.i2d_ig)} />
          <Metric label="FWHM(2D)" value={num(raman?.fwhm_2d_cm1)} unit="cm⁻¹" />
          <Metric label="C/O ratio" value={num(xps?.co_ratio)} />
        </dl>
      </div>

      {classification.evidence.length > 0 && (
        <details className="mt-3.5 border-t border-[color:var(--border)] pt-3">
          <summary className="cursor-pointer text-[13px] text-ink-secondary">
            Why this classification?
          </summary>
          <ul className="mt-2 space-y-1 text-[13px] text-ink-secondary">
            {classification.evidence.map((item) => (
              <li key={item.metric} className="flex gap-2">
                <span
                  aria-hidden="true"
                  className="mt-1.5 size-1.5 shrink-0 rounded-full"
                  style={{ background: 'var(--series-1)' }}
                />
                <span>
                  {item.detail}
                  {item.value !== null && (
                    <span className="tabular text-ink-muted"> ({num(item.value)})</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2.5 text-[12px] text-ink-muted">
            Runner-up forms:{' '}
            {Object.entries(classification.scores)
              .slice(1, 4)
              .map(([form, score]) => `${formLabel(form)} ${(score * 100).toFixed(0)}`)
              .join(' · ')}
          </p>
        </details>
      )}
    </Card>
  )
}

function ChemistryPanel({ report }: { report: AnalysisReport }) {
  const xps = report.xps
  if (!xps) return null
  return (
    <Card title="Surface chemistry" subtitle={`Regions analysed: ${xps.regions_analysed.join(', ')}`}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Metric label="C/O ratio" value={num(xps.co_ratio)} />
        <Metric label="Oxygen" value={num(xps.oxygen_at_pct)} unit="at%" />
        <Metric label="sp² fraction" value={num(xps.sp2_fraction)} />
        <Metric label="sp²/sp³" value={num(xps.sp2_sp3_ratio)} />
      </dl>

      {Object.keys(xps.functional_groups).length > 0 && (
        <div className="mt-3.5">
          <p className="mb-1.5 text-[12px] font-medium text-ink-muted">
            Oxygen functional groups (fraction of C 1s)
          </p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(xps.functional_groups).map(([name, fraction]) => (
              <Tag key={name}>
                {name} {(fraction * 100).toFixed(1)}%
              </Tag>
            ))}
          </div>
        </div>
      )}

      {xps.co_ratio_source && (
        <p className="mt-3 text-[12px] text-ink-muted">C/O derived from: {xps.co_ratio_source}.</p>
      )}
      {xps.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[12px] text-ink-muted">
          {xps.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function DFTPanel({ report }: { report: AnalysisReport }) {
  const dft = report.dft
  if (!dft) return null
  return (
    <Card
      title="Property overlay vs. DFT and experimental references"
      subtitle="Each measured property is compared against the closest matching reference record"
    >
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-[color:var(--border)] text-left text-ink-muted">
            <th className="py-1.5 pr-3 font-medium">Property</th>
            <th className="py-1.5 pr-3 font-medium">Measured</th>
            <th className="py-1.5 pr-3 font-medium">Reference</th>
            <th className="py-1.5 pr-3 font-medium">Deviation</th>
            <th className="py-1.5 font-medium">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {dft.comparisons.map((row) => (
            <tr key={row.property} className="border-b border-[color:var(--border)] last:border-0">
              <td className="py-1.5 pr-3 text-ink">{row.label}</td>
              <td className="tabular py-1.5 pr-3 text-ink">
                {num(row.measured)} <span className="text-ink-muted">{row.unit}</span>
              </td>
              <td className="py-1.5 pr-3 text-ink-secondary">
                <span className="tabular">{num(row.reference_value)}</span>
                <span className="block text-[11px] text-ink-muted">{row.reference_material}</span>
              </td>
              <td className="tabular py-1.5 pr-3 text-ink-secondary">
                {signedPct(row.percent_deviation)}
              </td>
              <td className="py-1.5">
                <VerdictBadge verdict={row.verdict} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {dft.notes.map((note) => (
        <p key={note} className="mt-2 text-[12px] text-ink-muted">
          {note}
        </p>
      ))}
    </Card>
  )
}

function CitationsPanel({ report }: { report: AnalysisReport }) {
  if (report.citations.length === 0) return null
  return (
    <Card title="Dataset citations" subtitle="Sources used in this comparison">
      <ol className="space-y-2 text-[12px]">
        {report.citations.map((citation, index) => (
          <li key={`${citation.source}-${index}`} className="flex gap-2">
            <span className="tabular shrink-0 text-ink-muted">[{index + 1}]</span>
            <span className="text-ink-secondary">
              {citation.source}
              {citation.doi && (
                <>
                  {' '}
                  <a
                    href={`https://doi.org/${citation.doi}`}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-[color:var(--series-1)] underline decoration-1 underline-offset-2"
                  >
                    doi:{citation.doi}
                  </a>
                </>
              )}
              {!citation.doi && citation.url && (
                <>
                  {' '}
                  <a
                    href={citation.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-[color:var(--series-1)] underline decoration-1 underline-offset-2"
                  >
                    {citation.url}
                  </a>
                </>
              )}
              <span className="text-ink-muted">
                {' '}
                · library version {citation.version}, retrieved {citation.retrieved}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </Card>
  )
}

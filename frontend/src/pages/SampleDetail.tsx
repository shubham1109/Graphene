import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { dateLabel, num, routeLabel } from '../lib/format'
import type { AnalysisReport, Sample, Spectrum, Technique, XPSRegion } from '../lib/types'
import { ReportView } from '../components/report/ReportView'
import { Banner, Button, Card, Empty, Field, Select, Spinner, Tag } from '../components/ui'

export function SampleDetail() {
  const { sampleId } = useParams<{ sampleId: string }>()
  const navigate = useNavigate()

  const [sample, setSample] = useState<Sample | null>(null)
  const [spectra, setSpectra] = useState<Spectrum[]>([])
  const [reports, setReports] = useState<AnalysisReport[]>([])
  const [activeReportId, setActiveReportId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [analysing, setAnalysing] = useState(false)

  const refresh = useCallback(async () => {
    if (!sampleId) return
    const [nextSample, nextSpectra, nextReports] = await Promise.all([
      api.getSample(sampleId),
      api.listSpectra(sampleId),
      api.listReports(sampleId),
    ])
    setSample(nextSample)
    setSpectra(nextSpectra)
    setReports(nextReports)
    setActiveReportId((current) => current ?? nextReports[0]?.id ?? null)
  }, [sampleId])

  useEffect(() => {
    setLoading(true)
    refresh()
      .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setLoading(false))
  }, [refresh])

  async function runAnalysis() {
    if (!sampleId) return
    setError(null)
    setNotice(null)
    setAnalysing(true)
    try {
      const report = await api.analyse(sampleId)
      setReports((current) => [report, ...current])
      setActiveReportId(report.id)
      setNotice('Analysis complete.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setAnalysing(false)
    }
  }

  async function removeSample() {
    if (!sampleId || !sample) return
    if (!window.confirm(`Delete "${sample.name}" and all of its spectra and reports?`)) return
    try {
      await api.deleteSample(sampleId)
      navigate('/')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  if (loading) {
    return (
      <Card>
        <Spinner label="Loading sample…" />
      </Card>
    )
  }

  if (!sample) {
    return (
      <Empty title="Sample not found">
        <Link to="/" className="text-[color:var(--series-1)] underline">
          Back to samples
        </Link>
      </Empty>
    )
  }

  const activeReport = reports.find((report) => report.id === activeReportId) ?? null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/" className="text-[13px] text-ink-secondary hover:text-ink">
            ← All samples
          </Link>
          <h1 className="mt-1 text-[20px] font-semibold text-ink">{sample.name}</h1>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Tag>{routeLabel(sample.production_route)}</Tag>
            {sample.feedstock && <Tag>Feedstock: {sample.feedstock}</Tag>}
            {sample.batch_id && <Tag>Batch {sample.batch_id}</Tag>}
            {sample.properties.bet_m2_g != null && (
              <Tag>BET {num(sample.properties.bet_m2_g)} m²/g</Tag>
            )}
            {sample.properties.flake_size_um != null && (
              <Tag>D50 {num(sample.properties.flake_size_um)} µm</Tag>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="primary"
            onClick={runAnalysis}
            disabled={analysing || spectra.length === 0}
            title={spectra.length === 0 ? 'Upload a spectrum first' : undefined}
          >
            {analysing ? <Spinner label="Analysing…" /> : 'Run analysis'}
          </Button>
          <Button variant="danger" onClick={removeSample}>
            Delete
          </Button>
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {notice && <Banner kind="info">{notice}</Banner>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,340px)_1fr]">
        <div className="space-y-4">
          <UploadPanel
            sampleId={sample.id}
            onUploaded={() => {
              setNotice(null)
              refresh().catch((cause) =>
                setError(cause instanceof Error ? cause.message : String(cause)),
              )
            }}
            onError={setError}
          />

          <Card title="Uploaded files" subtitle={`${spectra.length} spectrum file(s)`}>
            {spectra.length === 0 ? (
              <Empty title="Nothing uploaded yet" />
            ) : (
              <ul className="space-y-2">
                {spectra.map((spectrum) => (
                  <li
                    key={spectrum.id}
                    className="flex items-start justify-between gap-2 rounded-lg border border-[color:var(--border)] p-2.5"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-medium text-ink">
                        {spectrum.meta.filename}
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <Tag>{spectrum.technique.toUpperCase()}</Tag>
                        {spectrum.technique === 'xps' && <Tag>{spectrum.region}</Tag>}
                        <Tag>{spectrum.meta.n_points} pts</Tag>
                      </div>
                      {spectrum.meta.warnings.length > 0 && (
                        <p className="mt-1 text-[11px] text-[color:var(--status-warning)]">
                          {spectrum.meta.warnings.length} parser note(s)
                        </p>
                      )}
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${spectrum.meta.filename}`}
                      onClick={async () => {
                        try {
                          await api.deleteSpectrum(sample.id, spectrum.id)
                          await refresh()
                        } catch (cause) {
                          setError(cause instanceof Error ? cause.message : String(cause))
                        }
                      }}
                    >
                      ×
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {reports.length > 0 && (
            <Card title="Analysis history" subtitle="Compare runs over time">
              <ul className="space-y-1.5">
                {reports.map((report) => {
                  const active = report.id === activeReportId
                  return (
                    <li key={report.id}>
                      <button
                        type="button"
                        onClick={() => setActiveReportId(report.id)}
                        aria-pressed={active}
                        className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                          active
                            ? 'border-[color:var(--series-1)] bg-[color:var(--page)]'
                            : 'border-[color:var(--border)] hover:bg-[color:var(--page)]'
                        }`}
                      >
                        <p className="text-[13px] font-medium text-ink">
                          {report.classification.label}
                        </p>
                        <p className="text-[11px] text-ink-muted">{dateLabel(report.created_at)}</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          <Tag>I(D)/I(G) {num(report.raman?.id_ig)}</Tag>
                          <Tag>C/O {num(report.xps?.co_ratio)}</Tag>
                        </div>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </Card>
          )}
        </div>

        <div className="min-w-0">
          {activeReport ? (
            <ReportView report={activeReport} spectra={spectra} />
          ) : (
            <Card>
              <Empty title="No analysis yet">
                {spectra.length === 0
                  ? 'Upload a Raman or XPS export, then run the analysis.'
                  : 'Press “Run analysis” to fit the spectra and benchmark this sample.'}
              </Empty>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

const LASER_LINES = [325, 442, 457, 473, 488, 514.5, 532, 633, 660, 785, 830, 1064]

function UploadPanel({
  sampleId,
  onUploaded,
  onError,
}: {
  sampleId: string
  onUploaded: () => void
  onError: (message: string) => void
}) {
  const [technique, setTechnique] = useState<Technique>('raman')
  const [region, setRegion] = useState<XPSRegion>('unknown')
  const [excitation, setExcitation] = useState('532')
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return
      setBusy(true)
      try {
        for (const file of Array.from(files)) {
          if (technique === 'dft') {
            await api.uploadSpec(sampleId, file)
          } else {
            await api.uploadSpectrum(sampleId, file, technique, {
              region: technique === 'xps' ? region : undefined,
              excitationNm: technique === 'raman' && excitation ? Number(excitation) : undefined,
            })
          }
        }
        onUploaded()
        if (inputRef.current) inputRef.current.value = ''
      } catch (cause) {
        onError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setBusy(false)
      }
    },
    [sampleId, technique, region, excitation, onUploaded, onError],
  )

  return (
    <Card title="Upload data">
      <div className="space-y-3">
        <Field label="Data type">
          <Select
            value={technique}
            onChange={(event) => setTechnique(event.target.value as Technique)}
          >
            <option value="raman">Raman spectrum (CSV / TXT)</option>
            <option value="xps">XPS spectrum (CSV / TXT / VAMAS)</option>
            <option value="dft">Property or DFT spec sheet (JSON / CSV)</option>
          </Select>
        </Field>

        {technique === 'raman' && (
          <Field
            label="Excitation wavelength (nm)"
            hint="Crystallite size and defect density scale as λ⁴, so this matters. Read from the file header when present."
          >
            <Select value={excitation} onChange={(event) => setExcitation(event.target.value)}>
              {LASER_LINES.map((line) => (
                <option key={line} value={line}>
                  {line} nm
                </option>
              ))}
            </Select>
          </Field>
        )}

        {technique === 'xps' && (
          <Field
            label="Region"
            hint="Leave on auto-detect unless the energy range is ambiguous. VAMAS files carry their own region labels."
          >
            <Select
              value={region}
              onChange={(event) => setRegion(event.target.value as XPSRegion)}
            >
              <option value="unknown">Auto-detect</option>
              <option value="survey">Survey</option>
              <option value="c1s">C 1s</option>
              <option value="o1s">O 1s</option>
            </Select>
          </Field>
        )}

        <div
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            upload(event.dataTransfer.files)
          }}
          className={`rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
            dragging ? 'border-[color:var(--series-1)] bg-[color:var(--page)]' : 'border-[color:var(--border)]'
          }`}
        >
          {busy ? (
            <Spinner label="Parsing…" />
          ) : (
            <>
              <p className="text-[13px] text-ink-secondary">Drop a file here, or</p>
              <Button
                size="sm"
                className="mt-2"
                onClick={() => inputRef.current?.click()}
                type="button"
              >
                Choose file
              </Button>
              <input
                ref={inputRef}
                type="file"
                multiple
                className="hidden"
                accept=".csv,.txt,.dat,.asc,.json,.vms,.vamas,.npl,.tsv"
                onChange={(event) => upload(event.target.files)}
              />
            </>
          )}
        </div>

        <p className="text-[12px] text-ink-muted">
          Columns are auto-detected. Native instrument formats (Renishaw .wdf, Bruker .opus) are not
          supported — export to CSV or TXT first.
        </p>
      </div>
    </Card>
  )
}

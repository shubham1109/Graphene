import type { Verdict } from './types'

/** Significant-figure formatting that keeps very large/small values readable. */
export function num(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const magnitude = Math.abs(value)
  if (magnitude !== 0 && (magnitude >= 1e5 || magnitude < 1e-3)) {
    return value.toExponential(2).replace('e+', '×10^').replace('e-', '×10^-')
  }
  if (Number.isInteger(value) && magnitude < 1e5) return String(value)
  return Number(value.toPrecision(digits)).toString()
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

export function signedPct(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

/** Status colour + icon + label: never colour alone. */
export const VERDICT_META: Record<Verdict, { label: string; icon: string; color: string }> = {
  pass: { label: 'Pass', icon: '✓', color: 'var(--status-good)' },
  borderline: { label: 'Borderline', icon: '!', color: 'var(--status-warning)' },
  fail: { label: 'Fail', icon: '×', color: 'var(--status-critical)' },
  unknown: { label: 'Not assessed', icon: '–', color: 'var(--text-muted)' },
}

export function routeLabel(route: string): string {
  return route
    .split('_')
    .map((word) => (word === 'cvd' || word === 'sic' ? word.toUpperCase() : word))
    .join(' ')
    .replace(/^./, (c) => c.toUpperCase())
}

/** Classified-form and product-form keys as a spectroscopist would write them. */
const FORM_LABELS: Record<string, string> = {
  cvd_monolayer_film: 'CVD monolayer film',
  gnp_few_layer: 'GNP / few-layer',
  graphene_oxide: 'Graphene oxide',
  rgo: 'Reduced graphene oxide',
  flash_turbostratic: 'Flash / turbostratic',
  ink_dispersion: 'Ink / dispersion',
  monolayer: 'Monolayer',
  bilayer: 'Bilayer',
  few_layer: 'Few-layer',
  multilayer_gnp: 'Multilayer / GNP',
  reduced_graphene_oxide: 'Reduced graphene oxide',
  turbostratic: 'Turbostratic',
  amorphous_carbon: 'Amorphous carbon',
  indeterminate: 'Indeterminate',
}

export function formLabel(form: string): string {
  return FORM_LABELS[form] ?? form.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export function dateLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

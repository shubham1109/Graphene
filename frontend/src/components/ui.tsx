import clsx from 'clsx'
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { VERDICT_META } from '../lib/format'
import type { Verdict } from '../lib/types'

export function Card({
  children,
  className,
  title,
  subtitle,
  actions,
}: {
  children?: ReactNode
  className?: string
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <section
      className={clsx(
        'rounded-xl border bg-surface',
        'border-[color:var(--border)] shadow-[0_1px_2px_rgba(0,0,0,0.04)]',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-[color:var(--border)] px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[13px] text-ink-secondary">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}

export function Button({ variant = 'secondary', size = 'md', className, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={clsx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-2.5 py-1.5 text-[13px]' : 'px-3.5 py-2 text-sm',
        variant === 'primary' &&
          'bg-[color:var(--series-1)] text-white hover:brightness-110 active:brightness-95',
        variant === 'secondary' &&
          'border border-[color:var(--border)] bg-surface text-ink hover:bg-[color:var(--page)]',
        variant === 'ghost' && 'text-ink-secondary hover:bg-[color:var(--page)] hover:text-ink',
        variant === 'danger' &&
          'border border-[color:var(--status-critical)] text-[color:var(--status-critical)] hover:bg-[color:var(--status-critical)] hover:text-white',
        className,
      )}
    />
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] font-medium text-ink-secondary">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-ink-muted">{hint}</span>}
    </label>
  )
}

const controlClasses =
  'w-full rounded-lg border border-[color:var(--border)] bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(controlClasses, className)} />
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(controlClasses, className)} />
}

/** Status pill. Colour is always paired with an icon and a word. */
export function VerdictBadge({ verdict, className }: { verdict: Verdict; className?: string }) {
  const meta = VERDICT_META[verdict]
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] font-semibold',
        className,
      )}
      style={{ color: meta.color, background: `color-mix(in srgb, ${meta.color} 12%, transparent)` }}
    >
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  )
}

export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-md border border-[color:var(--border)] px-1.5 py-0.5 text-[12px] text-ink-secondary',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Banner({
  kind = 'info',
  title,
  children,
}: {
  kind?: 'info' | 'warning' | 'error'
  title?: string
  children: ReactNode
}) {
  const color =
    kind === 'error'
      ? 'var(--status-critical)'
      : kind === 'warning'
        ? 'var(--status-warning)'
        : 'var(--series-1)'
  const icon = kind === 'error' ? '×' : kind === 'warning' ? '!' : 'i'
  return (
    <div
      role={kind === 'error' ? 'alert' : 'status'}
      className="flex gap-2.5 rounded-lg border px-3.5 py-2.5 text-[13px]"
      style={{
        borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
        background: `color-mix(in srgb, ${color} 8%, transparent)`,
      }}
    >
      <span
        aria-hidden="true"
        className="mt-px flex size-4 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white"
        style={{ background: color }}
      >
        {icon}
      </span>
      <div className="min-w-0 text-ink-secondary">
        {title && <p className="font-semibold text-ink">{title}</p>}
        {children}
      </div>
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-ink-secondary">
      <span
        aria-hidden="true"
        className="size-3.5 animate-spin rounded-full border-2 border-[color:var(--border)] border-t-[color:var(--series-1)]"
      />
      {label}
    </span>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-[color:var(--border)] px-5 py-8 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-secondary">{children}</p>}
    </div>
  )
}

/** Screen-reader-friendly definition row used across the metric panels. */
export function Metric({
  label,
  value,
  unit,
  hint,
}: {
  label: string
  value: ReactNode
  unit?: string | null
  hint?: string
}) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[12px] text-ink-muted" title={label}>
        {label}
      </dt>
      <dd className="tabular mt-0.5 text-[17px] font-semibold text-ink">
        {value}
        {unit && <span className="ml-1 text-[12px] font-normal text-ink-secondary">{unit}</span>}
      </dd>
      {hint && <p className="mt-0.5 text-[11px] text-ink-muted">{hint}</p>}
    </div>
  )
}

import { useState } from 'react'
import { useAuth } from '../lib/auth'
import { Banner, Button, Card, Field, Input, Spinner } from '../components/ui'

export function Login() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [organisation, setOrganisation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, fullName, organisation)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
      <div className="mb-6">
        <h1 className="text-[22px] font-semibold text-ink">Graphene Benchmarker</h1>
        <p className="mt-1 text-[13px] text-ink-secondary">
          Benchmark your Raman, XPS and property data against open reference datasets, get
          recommended applications, and find the closest commercial products.
        </p>
      </div>

      <Card>
        <div className="mb-4 flex gap-1 rounded-lg bg-[color:var(--page)] p-1">
          {(['login', 'register'] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => {
                setMode(option)
                setError(null)
              }}
              aria-pressed={mode === option}
              className={`flex-1 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors ${
                mode === option ? 'bg-surface text-ink shadow-sm' : 'text-ink-secondary'
              }`}
            >
              {option === 'login' ? 'Sign in' : 'Create account'}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <Field label="Email">
            <Input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>

          <Field
            label="Password"
            hint={mode === 'register' ? 'At least 8 characters.' : undefined}
          >
            <Input
              type="password"
              required
              minLength={mode === 'register' ? 8 : undefined}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>

          {mode === 'register' && (
            <>
              <Field label="Full name (optional)">
                <Input value={fullName} onChange={(event) => setFullName(event.target.value)} />
              </Field>
              <Field label="Organisation (optional)">
                <Input
                  value={organisation}
                  onChange={(event) => setOrganisation(event.target.value)}
                />
              </Field>
            </>
          )}

          {error && <Banner kind="error">{error}</Banner>}

          <Button type="submit" variant="primary" className="w-full" disabled={busy}>
            {busy ? <Spinner /> : mode === 'login' ? 'Sign in' : 'Create account'}
          </Button>
        </form>
      </Card>
    </div>
  )
}

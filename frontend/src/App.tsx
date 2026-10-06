import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/auth'
import { Login } from './pages/Login'
import { SampleDetail } from './pages/SampleDetail'
import { Samples } from './pages/Samples'
import { ApplicationFinder } from './pages/ApplicationFinder'
import { Button, Spinner } from './components/ui'

type Theme = 'light' | 'dark' | 'system'
const THEME_KEY = 'graphene.theme'

function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_KEY) as Theme | null) ?? 'system',
  )

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  return { theme, setTheme }
}

export default function App() {
  const { user, loading, logout } = useAuth()
  const { theme, setTheme } = useTheme()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading…" />
      </div>
    )
  }

  if (!user) return <Login />

  return (
    <div className="min-h-screen">
      <header className="border-b border-[color:var(--border)] bg-surface">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-5">
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-semibold text-ink">Graphene Benchmarker</span>
              <span className="hidden text-[12px] text-ink-muted md:inline">Raman · XPS · DFT</span>
            </div>
            <nav className="flex items-center gap-1 text-[13px]">
              {[
                { to: '/', label: 'Samples' },
                { to: '/applications', label: 'Application finder' },
              ].map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    `rounded-md px-2.5 py-1 transition-colors ${
                      isActive
                        ? 'bg-[color:var(--page)] font-medium text-ink'
                        : 'text-ink-secondary hover:text-ink'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <label className="sr-only" htmlFor="theme">
              Colour theme
            </label>
            <select
              id="theme"
              value={theme}
              onChange={(event) => setTheme(event.target.value as Theme)}
              className="rounded-md border border-[color:var(--border)] bg-surface px-2 py-1 text-[12px] text-ink-secondary"
            >
              <option value="system">System theme</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
            <span className="hidden text-[13px] text-ink-secondary sm:inline">{user.email}</span>
            <Button size="sm" variant="ghost" onClick={logout}>
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-5 py-5">
        <Routes>
          <Route path="/" element={<Samples />} />
          <Route path="/samples/:sampleId" element={<SampleDetail />} />
          <Route path="/applications" element={<ApplicationFinder />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

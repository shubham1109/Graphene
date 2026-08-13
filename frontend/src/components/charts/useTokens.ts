import { useEffect, useState } from 'react'

export interface Tokens {
  surface: string
  page: string
  ink: string
  inkSecondary: string
  muted: string
  gridline: string
  baseline: string
  series: string[]
}

function read(): Tokens {
  const style = getComputedStyle(document.documentElement)
  const get = (name: string) => style.getPropertyValue(name).trim()
  return {
    surface: get('--surface-1') || '#fcfcfb',
    page: get('--page') || '#f9f9f7',
    ink: get('--text-primary') || '#0b0b0b',
    inkSecondary: get('--text-secondary') || '#52514e',
    muted: get('--text-muted') || '#898781',
    gridline: get('--gridline') || '#e1e0d9',
    baseline: get('--baseline') || '#c3c2b7',
    series: [get('--series-1'), get('--series-2'), get('--series-3'), get('--series-4')].map(
      (value, index) => value || ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'][index],
    ),
  }
}

/** Chart colours resolved from CSS variables, re-read when the theme changes. */
export function useTokens(): Tokens {
  const [tokens, setTokens] = useState<Tokens>(read)

  useEffect(() => {
    const refresh = () => setTokens(read())
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', refresh)
    // The theme toggle stamps data-theme on <html>; watch for it.
    const observer = new MutationObserver(refresh)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      media.removeEventListener('change', refresh)
      observer.disconnect()
    }
  }, [])

  return tokens
}

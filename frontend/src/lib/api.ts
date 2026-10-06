import type {
  AnalysisReport,
  ApplicationProduct,
  ApplicationProfile,
  CommercialProduct,
  ReferenceSpectrum,
  Sample,
  SampleProperties,
  Spectrum,
  TokenResponse,
  TdsMatchRecord,
  TdsMatchReport,
  TdsParameter,
  TdsSpecInput,
  TdsTemplate,
  Technique,
  User,
  XPSRegion,
} from './types'

const TOKEN_KEY = 'graphene.token'

export class ApiError extends Error {
  // Declared as a field rather than a parameter property: the project builds
  // with `erasableSyntaxOnly`, which forbids the constructor-parameter form.
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Pull a readable message out of FastAPI's several error shapes. */
function extractDetail(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload) return payload
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // 422 validation errors arrive as an array of {loc, msg, type}.
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) {
            const loc = 'loc' in item && Array.isArray(item.loc) ? item.loc.slice(1).join('.') : ''
            const msg = String((item as { msg: unknown }).msg)
            return loc ? `${loc}: ${msg}` : msg
          }
          return String(item)
        })
        .filter(Boolean)
      if (parts.length) return parts.join('; ')
    }
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    throw new ApiError('Could not reach the API. Is the backend running?', 0)
  }

  if (response.status === 401 && token) {
    // The token has expired or been revoked; drop it so the UI returns to login.
    setToken(null)
    window.dispatchEvent(new Event('graphene:unauthorised'))
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = text
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      /* keep the raw text for the error message */
    }
  }

  if (!response.ok) {
    throw new ApiError(
      extractDetail(payload, `${response.status} ${response.statusText}`),
      response.status,
    )
  }
  return payload as T
}

export const api = {
  register(body: { email: string; password: string; full_name?: string; organisation?: string }) {
    return request<TokenResponse>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  login(body: { email: string; password: string }) {
    return request<TokenResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  me() {
    return request<User>('/api/auth/me')
  },

  listSamples() {
    return request<Sample[]>('/api/samples')
  },

  createSample(body: {
    name: string
    production_route?: string
    feedstock?: string | null
    batch_id?: string | null
    notes?: string | null
    properties?: SampleProperties
  }) {
    return request<Sample>('/api/samples', { method: 'POST', body: JSON.stringify(body) })
  },

  getSample(id: string) {
    return request<Sample>(`/api/samples/${id}`)
  },

  updateSample(id: string, body: Partial<Sample>) {
    return request<Sample>(`/api/samples/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
  },

  deleteSample(id: string) {
    return request<void>(`/api/samples/${id}`, { method: 'DELETE' })
  },

  listSpectra(sampleId: string) {
    return request<Spectrum[]>(`/api/samples/${sampleId}/spectra`)
  },

  uploadSpectrum(
    sampleId: string,
    file: File,
    technique: Technique,
    options: { region?: XPSRegion; excitationNm?: number } = {},
  ) {
    const form = new FormData()
    form.append('file', file)
    form.append('technique', technique)
    if (options.region && options.region !== 'unknown') form.append('region', options.region)
    if (options.excitationNm) form.append('excitation_nm', String(options.excitationNm))
    return request<Spectrum[]>(`/api/samples/${sampleId}/spectra`, { method: 'POST', body: form })
  },

  uploadSpec(sampleId: string, file: File) {
    const form = new FormData()
    form.append('file', file)
    return request<Sample>(`/api/samples/${sampleId}/spec`, { method: 'POST', body: form })
  },

  deleteSpectrum(sampleId: string, spectrumId: string) {
    return request<void>(`/api/samples/${sampleId}/spectra/${spectrumId}`, { method: 'DELETE' })
  },

  analyse(sampleId: string) {
    return request<AnalysisReport>(`/api/samples/${sampleId}/analyse`, { method: 'POST' })
  },

  listReports(sampleId: string) {
    return request<AnalysisReport[]>(`/api/samples/${sampleId}/reports`)
  },

  listAllReports() {
    return request<AnalysisReport[]>('/api/reports')
  },

  referenceSpectra(technique?: 'raman' | 'xps') {
    const query = technique ? `?technique=${technique}` : ''
    return request<ReferenceSpectrum[]>(`/api/reference/spectra${query}`)
  },

  products(form?: string) {
    const query = form ? `?form=${form}` : ''
    return request<CommercialProduct[]>(`/api/reference/products${query}`)
  },

  // TDS application finder
  tdsParameters() {
    return request<TdsParameter[]>('/api/tds/parameters')
  },

  tdsApplications() {
    return request<ApplicationProfile[]>('/api/tds/applications')
  },

  tdsProducts(application?: string) {
    const query = application ? `?application=${encodeURIComponent(application)}` : ''
    return request<ApplicationProduct[]>(`/api/tds/products${query}`)
  },

  tdsTemplate() {
    return request<TdsTemplate>('/api/tds/template')
  },

  tdsPreview(body: TdsSpecInput, signal?: AbortSignal) {
    return request<TdsMatchReport>('/api/tds/preview', {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    })
  },

  tdsMatch(body: TdsSpecInput) {
    return request<TdsMatchRecord>('/api/tds/match', { method: 'POST', body: JSON.stringify(body) })
  },

  tdsMatches() {
    return request<TdsMatchRecord[]>('/api/tds/matches')
  },

  deleteTdsMatch(id: string) {
    return request<void>(`/api/tds/matches/${id}`, { method: 'DELETE' })
  },
}

export function datasheetUrl(file: string): string {
  return `/api/tds/datasheets/${encodeURIComponent(file)}`
}

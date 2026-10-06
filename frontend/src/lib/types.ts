export type Verdict = 'pass' | 'borderline' | 'fail' | 'unknown'
export type Technique = 'raman' | 'xps' | 'dft'
export type XPSRegion = 'survey' | 'c1s' | 'o1s' | 'unknown'

export interface User {
  id: string
  email: string
  full_name: string | null
  organisation: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface SampleProperties {
  flake_size_um?: number | null
  bet_m2_g?: number | null
  thickness_nm?: number | null
  bulk_density_g_cm3?: number | null
  carbon_purity_pct?: number | null
  band_gap_ev?: number | null
  youngs_modulus_gpa?: number | null
  carrier_mobility_cm2_vs?: number | null
  sheet_resistance_ohm_sq?: number | null
}

export const PRODUCTION_ROUTES = [
  'cvd',
  'liquid_phase_exfoliation',
  'mechanical_exfoliation',
  'electrochemical_exfoliation',
  'hummers_oxidation',
  'thermal_reduction',
  'chemical_reduction',
  'flash_joule_heating',
  'plasma',
  'sic_sublimation',
  'other',
  'unknown',
] as const
export type ProductionRoute = (typeof PRODUCTION_ROUTES)[number]

export interface Sample {
  id: string
  user_id: string
  name: string
  production_route: ProductionRoute
  feedstock: string | null
  batch_id: string | null
  notes: string | null
  properties: SampleProperties
  created_at: string
  updated_at: string
}

export interface ParseWarning {
  code: string
  message: string
}

export interface SpectrumMeta {
  filename: string
  technique: Technique
  region: XPSRegion
  x_label: string
  y_label: string
  x_unit: string
  n_points: number
  x_min: number
  x_max: number
  delimiter: string | null
  header_lines: number
  detected_format: string
  columns_detected: Record<string, string>
  instrument_hints: Record<string, string>
  excitation_nm: number | null
  warnings: ParseWarning[]
}

export interface Spectrum {
  id: string
  sample_id: string
  technique: Technique
  region: XPSRegion
  meta: SpectrumMeta
  preview_x: number[]
  preview_y: number[]
  created_at: string
}

export interface FittedPeak {
  name: string
  center_cm1: number
  height: number
  area: number
  fwhm_cm1: number
  shape: string
  center_stderr: number | null
  height_stderr: number | null
}

export interface RamanResult {
  spectrum_id: string | null
  excitation_nm: number
  peaks: FittedPeak[]
  id_ig: number | null
  id_ig_area: number | null
  i2d_ig: number | null
  i2d_ig_area: number | null
  idprime_ig: number | null
  fwhm_2d_cm1: number | null
  fwhm_g_cm1: number | null
  g_position_cm1: number | null
  two_d_position_cm1: number | null
  crystallite_size_la_nm: number | null
  defect_density_cm2: number | null
  mean_defect_distance_nm: number | null
  two_d_single_lorentzian: boolean | null
  two_d_lorentzian_r2: number | null
  two_d_four_component_r2: number | null
  estimated_layers: string | null
  fit_r_squared: number | null
  baseline_method: string
  notes: string[]
}

export interface XPSComponent {
  name: string
  assignment: string
  binding_energy_ev: number
  area: number
  fwhm_ev: number
  fraction_of_region: number
}

export interface XPSResult {
  spectrum_ids: string[]
  regions_analysed: string[]
  c1s_components: XPSComponent[]
  o1s_components: XPSComponent[]
  co_ratio: number | null
  co_ratio_source: string | null
  oxygen_at_pct: number | null
  carbon_at_pct: number | null
  sp2_fraction: number | null
  sp3_fraction: number | null
  sp2_sp3_ratio: number | null
  functional_groups: Record<string, number>
  pi_pi_star_present: boolean | null
  c1s_fit_r_squared: number | null
  o1s_fit_r_squared: number | null
  background_method: string
  notes: string[]
}

export interface PropertyComparison {
  property: string
  label: string
  unit: string
  measured: number
  reference_value: number
  reference_material: string
  reference_source: string
  percent_deviation: number
  verdict: Verdict
}

export interface DFTResult {
  comparisons: PropertyComparison[]
  notes: string[]
}

export interface ClassificationEvidence {
  metric: string
  value: number | null
  supports: string
  weight: number
  detail: string
}

export interface Classification {
  form: string
  label: string
  confidence: number
  scores: Record<string, number>
  evidence: ClassificationEvidence[]
  layer_estimate: string | null
  warnings: string[]
}

export interface CriterionCheck {
  metric: string
  label: string
  measured: number | string | null
  target: string
  verdict: Verdict
  comment: string
}

export interface ApplicationMatch {
  key: string
  name: string
  score: number
  verdict: Verdict
  checks: CriterionCheck[]
  mismatches: string[]
  rationale: string
}

export interface PeerFeatureDelta {
  feature: string
  label: string
  sample_value: number
  product_value: number
  percent_difference: number
  direction: string
}

export interface PeerMatch {
  product_id: string
  producer: string
  product_name: string
  form: string
  region: string | null
  datasheet_url: string | null
  distance: number
  similarity_pct: number
  features_compared: string[]
  deltas: PeerFeatureDelta[]
  summary: string
}

export interface ScorecardRow {
  metric: string
  label: string
  value: number | null
  unit: string | null
  target_range: string | null
  verdict: Verdict
  comment: string
  reference_percentile: number | null
}

export interface GlobalRanking {
  metric: string
  label: string
  sample_value: number
  percentile: number
  n_references: number
  better_is_lower: boolean
  reference_values: number[]
  reference_labels: string[]
}

export interface Citation {
  source: string
  doi: string | null
  url: string | null
  version: string
  retrieved: string
  used_for: string
}

export interface AnalysisReport {
  id: string
  user_id: string
  sample_id: string
  sample_name: string
  created_at: string
  engine_version: string
  raman: RamanResult | null
  xps: XPSResult | null
  dft: DFTResult | null
  classification: Classification
  scorecard: ScorecardRow[]
  rankings: GlobalRanking[]
  applications: ApplicationMatch[]
  peers: PeerMatch[]
  citations: Citation[]
  warnings: string[]
}

export interface Provenance {
  source: string
  doi: string | null
  url: string | null
  version: string
  licence: string | null
  retrieved: string
  note: string | null
}

export interface ReferenceSpectrum {
  key: string
  technique: string
  material_class: string
  label: string
  excitation_nm: number | null
  metrics: Record<string, number>
  peaks: Record<string, unknown>[]
  curve_x: number[]
  curve_y: number[]
  synthetic_curve: boolean
  provenance: Provenance
}

export interface CommercialProduct {
  key: string
  producer: string
  product_name: string
  form: string
  region: string | null
  datasheet_url: string | null
  specs: Record<string, number>
  spec_notes: Record<string, string>
  provenance: Provenance
}

// --------------------------------------------------------------------------- //
// TDS application finder
// --------------------------------------------------------------------------- //
export type MaterialFormKind = 'powder' | 'dispersion' | 'paste' | 'pellet' | 'unknown'
export type Confidence = 'high' | 'medium' | 'low'

export const MATERIAL_FORMS: { value: MaterialFormKind; label: string }[] = [
  { value: 'unknown', label: 'Not specified' },
  { value: 'powder', label: 'Powder' },
  { value: 'dispersion', label: 'Dispersion / ink' },
  { value: 'paste', label: 'Paste' },
  { value: 'pellet', label: 'Pellet / masterbatch' },
]

export const TDS_SPEC_KEYS = [
  'lateral_size_um',
  'layers',
  'thickness_nm',
  'bet_m2_g',
  'carbon_purity_pct',
  'oxygen_pct',
  'impurities_pct',
  'bulk_density_g_cm3',
  'electrical_conductivity_s_m',
  'thermal_conductivity_w_mk',
  'id_ig',
  'loading_wt_pct',
  'density_g_ml',
  'viscosity_cps',
  'sheet_resistance_ohm_sq',
  'hydrogen_pct',
  'thermal_stability_c',
] as const
export type TdsSpecKey = (typeof TDS_SPEC_KEYS)[number]

export type TdsSpecInput = {
  name?: string | null
  form: MaterialFormKind
  synthesis?: string | null
  surface_chemistry?: string | null
  solvent?: string | null
  orientation?: string | null
  crystallinity?: string | null
  solubility?: string | null
  notes?: string | null
} & Partial<Record<TdsSpecKey, string | number | null>>

/** The user's own TDS as transcribed on the server (seed/data/my_tds.json). */
export type TdsTemplate = TdsSpecInput & {
  company?: string
  product_id?: string
  source_file?: string
  methods?: Partial<Record<TdsSpecKey, string>>
}

export interface TdsParameter {
  key: TdsSpecKey
  label: string
  unit: string
  hint: string
  log_scale: boolean
}

export interface SpecInterval {
  low: number | null
  high: number | null
  text: string
}

export interface ParameterCheck {
  parameter: string
  label: string
  unit: string
  entered: string
  market_range: string
  typical: number | null
  n_products: number
  weight: number
  score: number
  verdict: Verdict
  comment: string
}

export interface ProductDelta {
  parameter: string
  label: string
  unit: string
  yours: string
  theirs: string
  relation: 'within' | 'higher' | 'lower'
}

export interface ProductSimilarity {
  key: string
  company: string
  product: string
  form: string
  similarity_pct: number
  shared_parameters: string[]
  deltas: ProductDelta[]
  application_tags: string[]
  applications_text: string | null
  datasheet_url: string | null
  datasheet_file: string | null
  summary: string
}

export interface ApplicationFit {
  key: string
  name: string
  description: string
  score: number
  verdict: Verdict
  confidence: Confidence
  evidence_count: number
  coverage: number
  form_match: boolean
  checks: ParameterCheck[]
  closest_products: ProductSimilarity[]
  strengths: string[]
  gaps: string[]
  rationale: string
}

export interface MarketPercentile {
  parameter: string
  label: string
  unit: string
  value: number
  percentile: number
  n_products: number
  comment: string
}

export interface TdsMatchReport {
  name: string | null
  form: MaterialFormKind
  entered: Record<string, SpecInterval>
  applications: ApplicationFit[]
  closest_products: ProductSimilarity[]
  market_context: MarketPercentile[]
  warnings: string[]
  database_size: number
  market_size: number
}

export interface TdsMatchRecord {
  id: string
  user_id: string
  created_at: string
  input: TdsSpecInput
  report: TdsMatchReport
}

export interface ApplicationProfile {
  key: string
  name: string
  description: string
  evidence_count: number
  companies: string[]
  products: string[]
  parameter_weights: Record<string, number>
  envelopes: Record<string, { low: number | null; high: number | null; typical: number | null; n: number; text: string }>
}

export interface ApplicationProduct {
  key: string
  company: string
  product: string
  form: string
  synthesis: string | null
  applications_text: string | null
  application_tags: string[]
  specs: Record<string, SpecInterval>
  surface_chemistry?: string
  solvent?: string
  additives?: string
  notes?: string
  datasheet_url: string | null
  datasheet_file: string | null
  corrections: { field: string; value: string | null; reason: string }[]
}

export interface Bridge {
  id: string
  name: string
  host: string
  app_key: string
  client_key: string
}

export interface EntertainmentArea {
  id: string
  name: string
  light_count: number
}

export interface Profile {
  id: string
  name: string
  lms_host: string
  lms_port: number
  player_name: string
  player_mac: string
  alsa_device: string
  bridge_id: string
  entertainment_area_id: string
  entertainment_area_name: string
  light_count: number
  color_mode: string
  sensitivity: number
  brightness_floor: number
  bars: number
  lower_cutoff_freq: number
  higher_cutoff_freq: number
  bass_hz: number
  mid_hz: number
  onset_delta: number
  onset_alpha: number
  exertion_clip: number
  enabled: boolean
}

export interface PlayerLatency {
  player_mac: string
  name: string | null
  strategy: string
  fixed_delay_ms: number
  speaker_ip: string | null
}

export interface LmsServer {
  host: string
  name: string
  port: number
}

export interface ApiStatus {
  version: string
  active_profile_id: string | null
  active_profile_name: string | null
  sync_master: string | null
  sync_master_name: string | null
  applied_delay_ms: number
  latency_warning: string | null
  processes: { squeezelite: boolean; cava: boolean }
  bridge_connected: boolean
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ': ' + body : ''}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function json(method: string, body: unknown, options?: RequestInit): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...options,
  }
}

// Bridges
export const getBridges = () => request<Bridge[]>('/api/bridges')
export const pairBridge = (host: string, name?: string) =>
  request<Bridge>('/api/bridges/pair', json('POST', { host, name }))
export const deleteBridge = (id: string) =>
  request<void>(`/api/bridges/${id}`, { method: 'DELETE' })
export const getBridgeAreas = (id: string) =>
  request<EntertainmentArea[]>(`/api/bridges/${id}/areas`)

// Profiles
export const getProfiles = () => request<Profile[]>('/api/profiles')
export const createProfile = (body: Omit<Profile, 'id' | 'player_mac'>) =>
  request<Profile>('/api/profiles', json('POST', body))
export const getProfile = (id: string) => request<Profile>(`/api/profiles/${id}`)
export const updateProfile = (id: string, body: Partial<Profile>) =>
  request<Profile>(`/api/profiles/${id}`, json('PATCH', body))
export const deleteProfile = (id: string) =>
  request<void>(`/api/profiles/${id}`, { method: 'DELETE' })
export const activateProfile = (id: string) =>
  request<{ active_id: string; warnings: string[] }>(`/api/profiles/${id}/activate`, { method: 'POST' })
export const deactivateProfile = () =>
  request<{ active_id: null }>('/api/profiles/deactivate', { method: 'POST' })
export const restartCava = (
  id: string,
  body: { lower_cutoff_freq?: number; higher_cutoff_freq?: number } = {}
) => request<{ ok: true }>(`/api/profiles/${id}/restart-cava`, json('POST', body))

// Player latencies
export const getPlayerLatencies = () => request<PlayerLatency[]>('/api/player-latencies')
export const createPlayerLatency = (body: {
  player_mac: string
  name?: string
  strategy?: string
  fixed_delay_ms?: number
}) => request<PlayerLatency>('/api/player-latencies', json('POST', body))
export const updatePlayerLatency = (
  mac: string,
  body: Partial<{ name: string; strategy: string; fixed_delay_ms: number }>
) => request<PlayerLatency>(`/api/player-latencies/${mac}`, json('PATCH', body))
export const deletePlayerLatency = (mac: string) =>
  request<void>(`/api/player-latencies/${mac}`, { method: 'DELETE' })

// LMS
export const discoverLms = () => request<LmsServer[]>('/api/lms/discover')

// Status
export const getStatus = () => request<ApiStatus>('/api/status')

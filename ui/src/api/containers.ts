import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

export interface ContainerInfo {
  id: string
  name: string
  image: string
  state: string
  profile: string
  ip?: string | null
  networks?: string[]
}

export function useContainers(all: boolean = true) {
  return useQuery({
    queryKey: ['containers', all],
    queryFn: () => apiGet<ContainerInfo[]>(`/containers?all=${all}`),
    refetchInterval: 3000,
  })
}

export function useInspectContainer(id: string | null) {
  return useQuery({
    queryKey: ['container', id],
    queryFn: () => apiGet<Record<string, unknown>>(`/containers/${id}`),
    enabled: !!id,
  })
}

/** POST /containers body — create/run a container. */
export interface RunContainerBody {
  image: string
  name?: string
  profile?: string
  start?: boolean
  detach?: boolean
  remove?: boolean
  command?: string[]
  kernel?: string
  cpus?: number
  memory?: string
  dns?: string[]
  volumes?: string[]
  mounts?: string[]
  env?: Record<string, string>
  workdir?: string
  confirm_kernel?: boolean
  // Container 1.3.1 run surface (SPEC phase 7 §6) — all optional
  arch?: string
  os?: string
  platform?: string
  rosetta?: boolean
  ssh?: boolean
  init?: boolean
  init_image?: string
  kernel_arg?: string[]
  masked_path?: string[]
  read_only_path?: string[]
  shm_size?: string
  ulimit?: string[]
  publish_socket?: string[]
  virtualization?: boolean
  runtime?: string
}

export function useRunContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RunContainerBody) => apiPost<ContainerInfo>('/containers', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

export function useStartContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiPost<{ status: string; state: string }>(`/containers/${id}/start`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

export function useStopContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiPost<{ status: string; state: string }>(`/containers/${id}/stop`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

export function useDestroyContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiDelete<{ status: string }>(`/containers/${id}?confirm=true`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

export function useKillContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, signal = 'KILL' }: { id: string; signal?: string }) =>
      apiPost<{ status: string; state: string }>(`/containers/${id}/kill?signal=${encodeURIComponent(signal)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

// ---------------------------------------------------------------------------
// Exec (container 1.3.1: detached, ulimit)
// ---------------------------------------------------------------------------

export interface ExecResult {
  exit_code: number
  stdout: string
  stderr: string
}

/** POST /containers/{id}/exec body (1.3.1 exec surface: detached, ulimit). */
export interface ExecRequest {
  command: string[]
  user?: string
  uid?: number
  gid?: number
  workdir?: string
  env?: Record<string, string>
  env_file?: string
  tty?: boolean
  interactive?: boolean
  timeout?: number
  detached?: boolean
  ulimit?: string[]
}

export async function execContainer(id: string, req: ExecRequest): Promise<ExecResult> {
  return apiPost<ExecResult>(`/containers/${encodeURIComponent(id)}/exec`, req)
}

// ---------------------------------------------------------------------------
// Stats, copy, export, prune (container 1.2.2)
// ---------------------------------------------------------------------------

export interface StatsSample {
  id: string
  cpu_usage_usec: number        // cumulative µs
  memory_usage_bytes: number
  memory_limit_bytes: number
  network_rx_bytes: number
  network_tx_bytes: number
  block_read_bytes: number
  block_write_bytes: number
  num_processes: number
  sampled_at: number            // time.monotonic() seconds
}

export function getStats(id?: string) {
  return apiGet<StatsSample[]>(id ? `/containers/${encodeURIComponent(id)}/stats` : '/stats')
}

export function useStats(id?: string) {
  return useQuery({
    queryKey: id ? ['stats', id] : ['stats'],
    queryFn: () => getStats(id),
    refetchInterval: 3000,
  })
}

/**
 * Derive per-container CPU utilisation (%) from two consecutive polls of
 * ``stats``.  ``cpu_usage_usec`` is cumulative, so the rate is
 * (Δcpu_usec/1e6)/Δt × 100, clamped to 0..100.  Returns a map keyed by
 * container id; ids seen only once (or with no time advance) are absent.
 */
export function useCpuPercentMap(stats: StatsSample[] | undefined): Record<string, number> {
  const prev = useRef<Record<string, { cpu_usage_usec: number; sampled_at: number }>>({})
  const [cpu, setCpu] = useState<Record<string, number>>({})

  useEffect(() => {
    if (!stats) return
    const next: Record<string, number> = {}
    const current: Record<string, { cpu_usage_usec: number; sampled_at: number }> = {}
    for (const s of stats) {
      const p = prev.current[s.id]
      if (p && s.sampled_at > p.sampled_at && s.cpu_usage_usec >= p.cpu_usage_usec) {
        const dt = s.sampled_at - p.sampled_at
        if (dt > 0) {
          const pct = ((s.cpu_usage_usec - p.cpu_usage_usec) / 1e6 / dt) * 100
          next[s.id] = Math.max(0, Math.min(100, pct))
        }
      }
      current[s.id] = { cpu_usage_usec: s.cpu_usage_usec, sampled_at: s.sampled_at }
    }
    prev.current = current
    setCpu(next)
  }, [stats])

  return cpu
}

// ---------------------------------------------------------------------------
// Stats history (phase 5 sampling engine)
// ---------------------------------------------------------------------------

/** A derived two-tick rate sample from the shared sampler (2 s cadence). */
export interface StatsRate {
  id: string
  cpu_percent: number        // derived rate, clamped 0..100
  memory_bytes: number
  memory_limit_bytes: number
  net_rx_delta: number
  net_tx_delta: number
  block_r_delta: number
  block_w_delta: number
  num_processes: number
  at: number                 // time.monotonic() — opaque ordering key
}

export interface StatsHistoryResponse {
  samples: StatsRate[]
  ids: string[]
}

/**
 * Trailing derived-rate history.  With ``containerId`` the server returns
 * only that container's series (oldest→newest); without, every
 * container's series merged into one list sorted by ``at`` (each sample
 * carries its ``id``).
 */
export function getStatsHistory(seconds = 120, containerId?: string) {
  const params = new URLSearchParams({ seconds: String(seconds) })
  if (containerId) params.set('container_id', containerId)
  return apiGet<StatsHistoryResponse>(`/stats/history?${params.toString()}`)
}

export function useStatsHistory(seconds = 120, containerId?: string) {
  return useQuery({
    queryKey: containerId ? ['stats-history', seconds, containerId] : ['stats-history', seconds],
    queryFn: () => getStatsHistory(seconds, containerId),
    refetchInterval: 5000,
  })
}

export type CopyDirection = 'from' | 'to'

export interface CopyResult {
  path: string
}

export function copyFile(id: string, source: string, destination: string, direction: CopyDirection) {
  return apiPost<CopyResult>(`/containers/${encodeURIComponent(id)}/copy`, { source, destination, direction })
}

export function useCopyFile() {
  return useMutation({
    mutationFn: ({ id, source, destination, direction }: { id: string; source: string; destination: string; direction: CopyDirection }) =>
      copyFile(id, source, destination, direction),
  })
}

export interface ExportResult {
  path: string
  container_id: string
  artifact: { name: string; path: string; kind: string }
}

export function exportFs(id: string, outputPath?: string) {
  return apiPost<ExportResult>(`/containers/${encodeURIComponent(id)}/export`, { output_path: outputPath ?? null })
}

export function useExportFs() {
  return useMutation({
    mutationFn: (id: string) => exportFs(id),
  })
}

export interface PruneResult {
  ids: string[]
  message: string
}

export function pruneContainers() {
  return apiPost<PruneResult>('/containers/prune', { confirm: true })
}

export function usePruneContainers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => pruneContainers(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['containers'] }),
  })
}

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

export function useRunContainer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      image: string; name?: string; profile?: string;
      start?: boolean; detach?: boolean; remove?: boolean; command?: string[];
      kernel?: string; cpus?: number; memory?: string; dns?: string[];
      volumes?: string[]; mounts?: string[];
      env?: Record<string, string>; workdir?: string;
      confirm_kernel?: boolean;
    }) => apiPost<ContainerInfo>('/containers', body),
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

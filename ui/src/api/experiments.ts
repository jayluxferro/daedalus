import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiDelete, API_BASE } from './client'

export interface ExperimentManifest {
  run_id: string
  created_at: string
  image: string
  image_digest: string
  profile: string
  kernel: string | null
  command: string[]
  container_name: string
  exit_code: number | null
  duration_seconds: number
  artifacts: { name: string; path: string; kind: string }[]
}

export function useExperiments() {
  return useQuery({
    queryKey: ['experiments'],
    queryFn: () => apiGet<ExperimentManifest[]>('/experiments'),
    refetchInterval: 10000,
  })
}

export function useExperiment(id: string | null) {
  return useQuery({
    queryKey: ['experiment', id],
    queryFn: () => apiGet<ExperimentManifest>(`/experiments/${id}`),
    enabled: !!id,
  })
}

export function useDeleteExperiment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      apiDelete<{ status: string }>(`/experiments/${runId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['experiments'] }),
  })
}

export function useClearExperiments() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiDelete<{ status: string; count: number }>('/experiments'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['experiments'] }),
  })
}

export interface ReportInfo {
  run_id: string
  image: string
  image_digest: string
  filesystem: {
    added_count: number
    modified_count: number
    removed_count: number
    total_changes: number
    added: string[]
    modified: string[]
    removed: string[]
  }
  network: {
    connections: unknown[]
    dns_queries: unknown[]
    listening_ports: unknown[]
    total_bytes_sent: number
    total_bytes_received: number
  }
  syscalls: {
    total_calls: number
    unique_syscalls: number
    top_calls: unknown[]
    suspicious_calls: unknown[]
  }
  resources: {
    peak_cpu_percent: number
    peak_memory_mb: number
    total_disk_writes: number
    total_disk_reads: number
    peak_pid_count: number
    duration_seconds: number
  }
  risk_score: number
  risk_factors: string[]
  findings: string[]
  generated_at: string
}

export async function getReport(runId: string): Promise<ReportInfo | null> {
  // 404 → null (no behavioral report for this run); any other failure throws.
  const res = await fetch(`${API_BASE}/experiments/${runId}/report`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

export function useReport(runId: string | null) {
  return useQuery({
    queryKey: ['experiment', runId, 'report'],
    queryFn: () => getReport(runId!),
    enabled: !!runId,
    staleTime: 60_000,
  })
}

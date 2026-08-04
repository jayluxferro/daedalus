import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiDelete } from './client'

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

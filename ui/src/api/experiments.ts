import { useQuery } from '@tanstack/react-query'
import { apiGet } from './client'

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

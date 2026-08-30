import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

export interface VolumeInfo {
  id: string
  name: string
  driver: string
  format: string
  size_bytes: number
  size_spec: string
  source: string
  created_date: string
  labels: Record<string, string>
  options: Record<string, string>
}

export interface VolumeCreateBody {
  name: string
  size?: string
  labels?: Record<string, string>
  options?: Record<string, string>
}

export function getVolumes() {
  return apiGet<VolumeInfo[]>('/volumes')
}

export function useVolumes() {
  return useQuery({
    queryKey: ['volumes'],
    queryFn: () => getVolumes(),
    refetchInterval: 3000,
  })
}

export function createVolume(body: VolumeCreateBody) {
  return apiPost<VolumeInfo>('/volumes', body)
}

export function useCreateVolume() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: VolumeCreateBody) => createVolume(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  })
}

export function getVolume(name: string) {
  return apiGet<VolumeInfo>(`/volumes/${encodeURIComponent(name)}`)
}

export function deleteVolume(name: string) {
  return apiDelete<{ status: string; name: string }>(`/volumes/${encodeURIComponent(name)}?confirm=true`)
}

export function useDeleteVolume() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => deleteVolume(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  })
}

export function pruneVolumes() {
  return apiPost<{ removed: string[] }>('/volumes/prune', { confirm: true })
}

export function usePruneVolumes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => pruneVolumes(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['volumes'] }),
  })
}

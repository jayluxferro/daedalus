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

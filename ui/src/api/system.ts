import { useQuery } from '@tanstack/react-query'
import { apiGet } from './client'

export interface SystemStatus {
  container_version: string
  container_commit: string
  apiserver_running: boolean
  container_count: number
  running_count: number
  disk_usage: { total?: number; used?: number; free?: number; error?: string }
  capabilities: Record<string, unknown>
}

export interface ProfileInfo {
  name: string
  description: string
  kernel: string | null
  no_dns: boolean
  dns: string[]
  dns_domain: string[]
  tmpfs: string[]
  cpus: number | null
  memory: string | null
}

export function useSystemStatus() {
  return useQuery({
    queryKey: ['system-status'],
    queryFn: () => apiGet<SystemStatus>('/system/status'),
    refetchInterval: 5000,
  })
}

export function useProfiles() {
  return useQuery({
    queryKey: ['profiles'],
    queryFn: () => apiGet<ProfileInfo[]>('/profiles'),
  })
}

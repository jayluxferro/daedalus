import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

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

export function useDnsList() {
  return useQuery({
    queryKey: ['dns'],
    queryFn: () => apiGet<string[]>('/system/dns'),
    refetchInterval: 10000,
  })
}

export function useDnsCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (domain: string) =>
      apiPost<{ status: string; domain: string }>(`/system/dns?domain=${encodeURIComponent(domain)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns'] }),
  })
}

export function useDnsDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (domain: string) =>
      apiDelete<{ status: string }>(`/system/dns/${encodeURIComponent(domain)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns'] }),
  })
}

export function useBuilderStatus() {
  return useQuery({
    queryKey: ['builder-status'],
    queryFn: () => apiGet<Record<string, unknown>>('/builder/status'),
    refetchInterval: 15000,
  })
}

export function useSystemRestart() {
  return useMutation({
    mutationFn: () => apiPost<{ status: string }>('/system/restart'),
  })
}

// ---------------------------------------------------------------------------
// System version + properties (phase 9)
// ---------------------------------------------------------------------------

/** One app's version block — mirrors ``container system version --format json``. */
export interface SystemVersionEntry {
  version: string
  commit: string
  buildType: string
}

/** Keyed by app name (e.g. ``container``, ``container-apiserver``). */
export type SystemVersion = Record<string, SystemVersionEntry>

export function getSystemVersion() {
  return apiGet<SystemVersion>('/system/version')
}

/**
 * Version only changes on an upgrade — fetch once and treat as near-static.
 * (No refetchInterval; staleTime is long.)
 */
export function useSystemVersion() {
  return useQuery({
    queryKey: ['system-version'],
    queryFn: getSystemVersion,
    staleTime: 5 * 60 * 1000,
  })
}

export function getSystemProperties() {
  return apiGet<{ properties: Record<string, string> }>('/system/properties')
}

/** System property key-values are host configuration — near-static. */
export function useSystemProperties() {
  return useQuery({
    queryKey: ['system-properties'],
    queryFn: getSystemProperties,
    staleTime: 5 * 60 * 1000,
  })
}

// ---------------------------------------------------------------------------
// Registry (phase 1 API surface, surfaced in the UI for phase 9)
// ---------------------------------------------------------------------------

export function getRegistryDefault() {
  return apiGet<{ host: string }>('/registry/default')
}

export function useRegistryDefault() {
  return useQuery({
    queryKey: ['registry-default'],
    queryFn: getRegistryDefault,
    refetchInterval: 10000,
  })
}

export interface RegistryLoginBody {
  server: string
  username?: string
  password?: string
  scheme?: string
}

export function useRegistryLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: RegistryLoginBody) =>
      apiPost<{ status: string; server: string }>('/registry/login', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['registry-default'] }),
  })
}

export function useRegistryLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (server: string) =>
      apiPost<{ status: string; server: string }>(`/registry/logout?server=${encodeURIComponent(server)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['registry-default'] }),
  })
}

export function useRegistryDefaultSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ host, scheme }: { host: string; scheme?: string }) =>
      apiPost<{ status: string; host: string; scheme?: string | null }>('/registry/default', { host, scheme }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['registry-default'] }),
  })
}

export function useRegistryDefaultUnset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiDelete<{ status: string }>('/registry/default'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['registry-default'] }),
  })
}

// ---------------------------------------------------------------------------
// Networks (container 1.2.2)
// ---------------------------------------------------------------------------

export interface NetworkInfo {
  id: string
  name: string
  plugin: string
  mode: string
  subnet: string
  subnet_v6: string
  gateway: string
}

export interface NetworkCreateBody {
  name: string
  subnet?: string
  subnet_v6?: string
  internal: boolean
  plugin?: string
  options?: Record<string, string>
}

export function getNetworks() {
  return apiGet<NetworkInfo[]>('/networks')
}

export function useNetworks() {
  return useQuery({
    queryKey: ['networks'],
    queryFn: () => getNetworks(),
    refetchInterval: 3000,
  })
}

export function createNetwork(body: NetworkCreateBody) {
  return apiPost<NetworkInfo>('/networks', body)
}

export function useCreateNetwork() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: NetworkCreateBody) => createNetwork(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['networks'] }),
  })
}

export function getNetwork(name: string) {
  return apiGet<NetworkInfo>(`/networks/${encodeURIComponent(name)}`)
}

export function deleteNetwork(name: string) {
  return apiDelete<{ status: string; name: string }>(`/networks/${encodeURIComponent(name)}?confirm=true`)
}

export function useDeleteNetwork() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => deleteNetwork(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['networks'] }),
  })
}

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

export interface MachineInfo {
  id: string
  status: string
  cpus: number
  memory_bytes: number
  disk_size_bytes: number
  default: boolean
  created_date: string
  home_mount: string
  image_ref: string
  platform_arch: string
  platform_os: string
  user_username: string
  user_uid: number
  user_gid: number
  warning: string // advisor note (non-empty when the image likely lacks /sbin/init)
}

export interface MachineCreateBody {
  image: string
  name?: string
  cpus?: number
  memory?: string
  home_mount?: string
  virtualization: boolean
  kernel?: string
  set_default: boolean
  no_boot: boolean
}

export interface MachineSetBody {
  cpus?: number
  memory?: string
  home_mount?: string
  virtualization?: boolean
  kernel?: string
}

export interface MachineRunResult {
  exit_code: number
  stdout: string
  stderr: string
  explanation?: string // missing-init explanation when the run failed
}

export function getMachines() {
  return apiGet<MachineInfo[]>('/machines')
}

export function useMachines() {
  return useQuery({
    queryKey: ['machines'],
    queryFn: () => getMachines(),
    refetchInterval: 3000,
  })
}

export function createMachine(body: MachineCreateBody) {
  return apiPost<MachineInfo>('/machines', body)
}

export function useCreateMachine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MachineCreateBody) => createMachine(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

export function getMachine(id: string) {
  return apiGet<MachineInfo>(`/machines/${encodeURIComponent(id)}`)
}

export function useInspectMachine(id: string | null) {
  return useQuery({
    queryKey: ['machine', id],
    queryFn: () => getMachine(id!),
    enabled: !!id,
  })
}

export function setMachine(id: string, body: MachineSetBody) {
  return apiPost<{ ok: boolean }>(`/machines/${encodeURIComponent(id)}/set`, body)
}

export function useSetMachine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MachineSetBody }) => setMachine(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

export function setMachineDefault(id: string) {
  return apiPost<{ ok: boolean }>(`/machines/${encodeURIComponent(id)}/set-default`)
}

export function useSetMachineDefault() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => setMachineDefault(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

export function stopMachine(id: string) {
  return apiPost<{ ok: boolean }>(`/machines/${encodeURIComponent(id)}/stop`)
}

export function useStopMachine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => stopMachine(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

export function deleteMachine(id: string) {
  return apiDelete<{ ok: boolean }>(`/machines/${encodeURIComponent(id)}?confirm=true`)
}

export function useDeleteMachine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteMachine(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

/** POST /machines/{id}/run body (1.3.1 machine-run surface: root, ulimit, detached, tty, user, workdir). */
export interface MachineRunBody {
  command: string[]
  root?: boolean
  ulimit?: string[]
  detached?: boolean
  tty?: boolean
  user?: string
  workdir?: string
}

/** GET /machines/{id}/logs query options (1.3.1: boot, tail). */
export interface MachineLogsQuery {
  boot?: boolean
  tail?: number
}

export function runMachine(id: string, body: MachineRunBody) {
  return apiPost<MachineRunResult>(`/machines/${encodeURIComponent(id)}/run`, body)
}

export function useRunMachine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MachineRunBody }) => runMachine(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['machines'] }),
  })
}

export function getMachineLogs(id: string, opts: MachineLogsQuery = {}) {
  const params = new URLSearchParams()
  if (opts.boot) params.set('boot', 'true')
  if (opts.tail !== undefined && opts.tail > 0) params.set('tail', String(opts.tail))
  const qs = params.toString()
  return apiGet<{ logs: string }>(`/machines/${encodeURIComponent(id)}/logs${qs ? '?' + qs : ''}`)
}

export function useMachineLogs(id: string | null, opts: MachineLogsQuery = {}) {
  return useQuery({
    queryKey: ['machine-logs', id, opts.boot ?? false, opts.tail ?? null],
    queryFn: () => getMachineLogs(id!, opts),
    enabled: !!id,
  })
}

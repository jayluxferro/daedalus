import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

/** One node container of a k8s cluster (SPEC phase 8 §2: control-plane + workers). */
export interface ClusterNode {
  id: string
  cluster_name: string
  role: string | null
  is_control_plane: boolean
  is_running: boolean
  address?: string | null
}

/**
 * A grouped k8s cluster (K8sCluster.to_dict()).
 *
 * The live API nests nodes as `control_plane` + `workers`; `nodes` is
 * tolerated as an alternate wire shape.
 */
export interface ClusterInfo {
  name: string
  id: string
  status: string // running | stopped | partial | unknown
  is_running: boolean
  control_plane?: ClusterNode | null
  workers?: ClusterNode[]
  nodes?: ClusterNode[]
}

/** Body for POST /clusters (container 1.3.1 `k8s create`). */
export interface ClusterCreateBody {
  name: string
  node_image?: string
  cpus?: number
  memory?: string
  remove?: boolean // --rm: remove node containers when the cluster stops
}

/** Body for POST /clusters/{name}/load-image. */
export interface ClusterLoadImageBody {
  image: string
  platform?: string
}

export function getClusters() {
  return apiGet<ClusterInfo[]>('/clusters')
}

export function useClusters() {
  return useQuery({
    queryKey: ['clusters'],
    queryFn: () => getClusters(),
    refetchInterval: 3000,
  })
}

export function createCluster(body: ClusterCreateBody) {
  return apiPost<{ name: string }>('/clusters', body)
}

export function useCreateCluster() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ClusterCreateBody) => createCluster(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  })
}

export function startCluster(name: string) {
  return apiPost<{ ok: boolean }>(`/clusters/${encodeURIComponent(name)}/start`)
}

export function useStartCluster() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => startCluster(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  })
}

export function loadImageIntoCluster(name: string, body: ClusterLoadImageBody) {
  return apiPost<{ ok: boolean }>(`/clusters/${encodeURIComponent(name)}/load-image`, body)
}

export function useLoadImageIntoCluster() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: ClusterLoadImageBody }) => loadImageIntoCluster(name, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  })
}

export function getClusterKubeconfig(name: string) {
  return apiGet<{ kubeconfig: string }>(`/clusters/${encodeURIComponent(name)}/kubeconfig`)
}

export function useClusterKubeconfig(name: string | null) {
  return useQuery({
    queryKey: ['cluster-kubeconfig', name],
    queryFn: () => getClusterKubeconfig(name!),
    enabled: !!name,
  })
}

export function deleteCluster(name: string, confirm = true) {
  return apiDelete<{ ok: boolean }>(`/clusters/${encodeURIComponent(name)}?confirm=${confirm}`)
}

export function useDeleteCluster() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => deleteCluster(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clusters'] }),
  })
}

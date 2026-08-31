import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiDelete } from './client'

export interface ImageInfo {
  name: string
  tag: string
  size: number
  digest: string
  id: string
}

/** Image reference for API delete — backend expects repository name without tag. */
export function imageRef(img: ImageInfo): string {
  return img.name
}

export function useImages() {
  return useQuery({
    queryKey: ['images'],
    queryFn: () => apiGet<ImageInfo[]>('/images'),
    refetchInterval: 10000,
  })
}

export function usePullImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (image: string) =>
      apiPost<{ status: string; name: string; id: string }>(`/images/pull?image=${encodeURIComponent(image)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function useDeleteImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      apiDelete<{ status: string }>(`/images/${encodeURIComponent(name)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function useInspectImage(name: string | null) {
  return useQuery({
    queryKey: ['image', name],
    queryFn: () => apiGet<{ name: string; id: string; digest: string; size: number; raw: Record<string, unknown> }>(
      `/images/${encodeURIComponent(name!)}`,
    ),
    enabled: !!name,
  })
}

export interface BuildImageRequest {
  tag: string
  context?: string
  file?: string
  target?: string
  arch?: string
  no_cache?: boolean
}

export function useBuildImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: BuildImageRequest) =>
      apiPost<{ status: string; name: string; id: string }>('/images/build', req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function usePushImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (image: string) =>
      apiPost<{ status: string; name: string }>(`/images/push?image=${encodeURIComponent(image)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function useLoadImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (path: string) =>
      apiPost<{ status: string; name: string; id: string }>(`/images/load?path=${encodeURIComponent(path)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function useSaveImage() {
  return useMutation({
    mutationFn: ({ image, output }: { image: string; output: string }) =>
      apiPost<{ status: string; image: string; path: string }>(
        `/images/save?image=${encodeURIComponent(image)}&output=${encodeURIComponent(output)}`,
      ),
  })
}

export function useTagImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ source, target }: { source: string; target: string }) =>
      apiPost<{ status: string }>('/images/tag', { source, target }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

export function usePruneImages() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<{ status: string; removed: string[]; count: number }>('/images/prune'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['images'] }),
  })
}

// ---------------------------------------------------------------------------
// Docker Hub search (phase 9) — host-side direct fetch, no backend hop.
// CORS / network failures are expected on some hosts; callers degrade.
// ---------------------------------------------------------------------------

export interface HubImageResult {
  repo_name: string
  short_description: string
  star_count: number
}

export interface HubSearchResponse {
  count: number
  results: HubImageResult[]
}

/**
 * Search Docker Hub via the daedalus API (server-side fetch — Hub sends no
 * CORS headers, so the browser cannot call it directly).
 */
export async function searchImages(query: string, limit = 10): Promise<HubImageResult[]> {
  const res = await fetch(`/images/search?q=${encodeURIComponent(query)}&limit=${limit}`)
  if (!res.ok) throw new Error(`image search: HTTP ${res.status}`)
  return (await res.json()) as HubImageResult[]
}

/** Enabled once the query is at least 2 characters. */
export function useImageSearch(query: string, limit = 10) {
  return useQuery({
    queryKey: ['image-search', query.trim(), limit],
    queryFn: () => searchImages(query, limit),
    enabled: query.trim().length >= 2,
    staleTime: 60 * 1000,
  })
}

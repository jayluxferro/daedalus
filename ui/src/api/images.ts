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

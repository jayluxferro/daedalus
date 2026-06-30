import { useMutation } from '@tanstack/react-query'
import { apiPost } from './client'

export interface TopologyValidateResult {
  valid: boolean
  name: string
  description: string
  networks: number
  attachments: number
  dns_entries: number
  internal: boolean
}

export function useValidateTopology() {
  return useMutation({
    mutationFn: (content: string) =>
      apiPost<TopologyValidateResult>('/topology/validate', { content }),
  })
}

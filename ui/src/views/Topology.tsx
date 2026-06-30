import { useState } from 'react'
import { useValidateTopology } from '../api/topology'
import { Box, GlassBox } from './shared'

const SAMPLE = `name: lab-net
description: victim + attacker on internal subnet
networks:
  - name: internal
    subnet: 10.89.0.0/24
    internal: true
attachments:
  - container: victim
    network: internal
  - container: attacker
    network: internal
dns_entries:
  - domain: c2.evil
    target: 10.89.0.2
`

export default function TopologyView() {
  const [content, setContent] = useState(SAMPLE)
  const validate = useValidateTopology()

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>Topology</h2>
      <p style={{ fontSize: 13, color: 'var(--muted-fg)', marginBottom: 20 }}>
        Validate Talos YAML templates. Networks are not created until Apple <code>container network</code> ships.
      </p>
      <GlassBox>
        <textarea
          value={content}
          onChange={e => setContent(e.target.value)}
          style={{
            width: '100%', minHeight: 280, fontFamily: 'monospace', fontSize: 12,
            background: '#0f0f13', color: 'var(--fg)', border: '1px solid var(--border)',
            borderRadius: 8, padding: 12, boxSizing: 'border-box', resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            onClick={() => validate.mutate(content)}
            disabled={validate.isPending || !content.trim()}
            style={{ padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13 }}
          >
            {validate.isPending ? 'Validating...' : 'Validate'}
          </button>
          <button onClick={() => setContent(SAMPLE)} style={{ padding: '8px 16px', background: 'transparent', color: 'var(--fg)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', fontSize: 13 }}>
            Load sample
          </button>
        </div>
        {validate.isError && (
          <p style={{ color: 'var(--danger)', fontSize: 12, marginTop: 12 }}>{(validate.error as Error).message}</p>
        )}
        {validate.data && (
          <pre style={{ fontSize: 12, marginTop: 12, background: '#0f0f13', padding: 12, borderRadius: 8, overflow: 'auto' }}>
            {JSON.stringify(validate.data, null, 2)}
          </pre>
        )}
      </GlassBox>
    </Box>
  )
}

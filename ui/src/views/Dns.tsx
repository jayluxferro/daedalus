import { useState } from 'react'
import { useDnsList, useDnsCreate, useDnsDelete } from '../api/system'
import { Box, GlassBox } from './shared'

export default function DnsView() {
  const { data: domains, isLoading } = useDnsList()
  const [newDomain, setNewDomain] = useState('')
  const create = useDnsCreate()
  const remove = useDnsDelete()

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>System DNS</h2>
      <p style={{ fontSize: 13, color: 'var(--muted-fg)', marginBottom: 20 }}>
        Local DNS domains resolvable on this Mac (for deception labs). Requires administrator.
      </p>

      <GlassBox style={{ marginBottom: 16 }}>
        <form onSubmit={e => {
          e.preventDefault()
          create.mutate(newDomain, { onSuccess: () => setNewDomain('') })
        }} style={{ display: 'flex', gap: 8 }}>
          <input
            value={newDomain}
            onChange={e => setNewDomain(e.target.value)}
            placeholder="lab.local"
            style={{ flex: 1, padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13 }}
          />
          <button type="submit" disabled={!newDomain || create.isPending} style={{ padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13 }}>
            Add domain
          </button>
        </form>
        {create.isError && <p style={{ color: 'var(--danger)', fontSize: 12, marginTop: 8 }}>{(create.error as Error).message}</p>}
      </GlassBox>

      <GlassBox>
        {isLoading ? (
          <p style={{ color: 'var(--muted-fg)', fontSize: 13 }}>Loading...</p>
        ) : !domains?.length ? (
          <p style={{ color: 'var(--muted-fg)', fontSize: 13 }}>No local DNS domains configured.</p>
        ) : (
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {domains.map(d => (
              <li key={d} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                <code style={{ fontSize: 13 }}>{d}</code>
                <button
                  onClick={() => { if (confirm(`Delete DNS domain ${d}?`)) remove.mutate(d) }}
                  disabled={remove.isPending}
                  style={{ padding: '4px 10px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--danger)', cursor: 'pointer' }}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </GlassBox>
    </Box>
  )
}

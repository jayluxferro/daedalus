import { useProfiles } from '../api/system'
import { Box, GlassBox } from './shared'

export default function ProfilesView() {
  const { data: profiles, isLoading } = useProfiles()

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Security Profiles</h2>

      {isLoading ? (
        <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
          {profiles?.map(p => (
            <GlassBox key={p.name}>
              <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{p.name}</h3>
              <p style={{ fontSize: 12, color: 'var(--muted-fg)', marginBottom: 12, lineHeight: 1.5 }}>{p.description}</p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 11 }}>
                {p.no_dns && <Tag>No DNS</Tag>}
                {p.dns.length > 0 && <Tag>DNS: {p.dns.join(', ')}</Tag>}
                {p.dns_domain.length > 0 && <Tag>Domain: {p.dns_domain.join(', ')}</Tag>}
                {p.kernel && <Tag>Kernel: {p.kernel}</Tag>}
                {p.tmpfs.length > 0 && <Tag>tmpfs: {p.tmpfs.join(', ')}</Tag>}
                {p.cpus && <Tag>CPUs: {p.cpus}</Tag>}
                {p.memory && <Tag>Mem: {p.memory}</Tag>}
              </div>
            </GlassBox>
          ))}
        </div>
      )}
    </Box>
  )
}

function Tag({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{ padding: '2px 8px', background: color || '#1c1c22', borderRadius: 4, color: 'var(--muted-fg)', whiteSpace: 'nowrap' }}>
      {children}
    </span>
  )
}

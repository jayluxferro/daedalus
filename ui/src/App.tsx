import { useState } from 'react'
import Dashboard from './views/Dashboard'
import ContainersView from './views/Containers'
import ImagesView from './views/Images'
import LogsView from './views/Logs'
import AuditView from './views/Audit'
import ProfilesView from './views/Profiles'
import ExperimentsView from './views/Experiments'
import TopologyView from './views/Topology'
import TerminalView from './views/Terminal'

type Page = 'dashboard' | 'containers' | 'terminal' | 'images' | 'logs' | 'audit' | 'profiles' | 'experiments' | 'topology'

const NAV: { id: Page; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'containers', label: 'Containers' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'images', label: 'Images' },
  { id: 'logs', label: 'Logs' },
  { id: 'experiments', label: 'Experiments' },
  { id: 'topology', label: 'Topology' },
  { id: 'audit', label: 'Audit' },
  { id: 'profiles', label: 'Profiles' },
]

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [terminalId, setTerminalId] = useState<string | null>(null)

  const navigate = (p: Page) => {
    if (p === 'terminal' && !terminalId) {
      setPage('containers')
      return
    }
    setPage(p)
  }

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw' }}>
      <nav style={{
        width: 200, background: '#0f0f13', borderRight: '1px solid #27272a',
        display: 'flex', flexDirection: 'column', flexShrink: 0,
      }}>
        <div style={{ padding: '16px', borderBottom: '1px solid #27272a' }}>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: '#fafafa', margin: 0 }}>☿ DAEDALUS</h1>
          <p style={{ fontSize: 11, color: '#71717a', marginTop: 4 }}>Labyrinth Control Center</p>
        </div>
        {NAV.map(item => (
          <button key={item.id} onClick={() => navigate(item.id)} title={item.id === 'terminal' && !terminalId ? 'Select a running container first' : undefined} style={{
            all: 'unset', padding: '10px 16px', fontSize: 13, cursor: 'pointer',
            color: page === item.id ? '#fafafa' : '#a1a1aa',
            background: page === item.id ? '#1c1c22' : 'transparent',
            borderLeft: page === item.id ? '2px solid #3b82f6' : '2px solid transparent',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            {item.label}
          </button>
        ))}
      </nav>

      <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {page === 'dashboard' && <Dashboard />}
        {page === 'containers' && <ContainersView onOpenTerminal={id => { setTerminalId(id); setPage('terminal') }} />}
        {page === 'terminal' && terminalId && <TerminalView containerId={terminalId} onClose={() => setPage('containers')} />}
        {page === 'images' && <ImagesView />}
        {page === 'logs' && <LogsView />}
        {page === 'audit' && <AuditView />}
        {page === 'experiments' && <ExperimentsView />}
        {page === 'topology' && <TopologyView />}
        {page === 'profiles' && <ProfilesView />}
      </main>
    </div>
  )
}

import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { useCopyFile, execContainer } from '../api/containers'
import type { CopyDirection, ExecRequest } from '../api/containers'
import { Box, GlassBox } from './shared'

const inputStyle: React.CSSProperties = {
  flex: 1, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
  background: '#0f0f13', color: 'var(--fg)', fontSize: 12, boxSizing: 'border-box',
  minWidth: 0,
}
const selectStyle: React.CSSProperties = {
  padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
  background: '#0f0f13', color: 'var(--fg)', fontSize: 12,
}
const copyBtnStyle: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
  background: '#3b82f6', color: 'white', cursor: 'pointer', fontSize: 12,
  fontWeight: 500,
}

interface TerminalViewProps {
  containerId: string
  onClose?: () => void
}

const PROMPT = '\x1b[36mλ \x1b[0m'

interface ExecOptionField {
  key: string
  label: string
  kind: 'text' | 'bool' | 'repeat'
  placeholder?: string
}

// Exec-option registry (SPEC phase 7 §7: detach, ulimit, user, workdir).
// Same data-driven pattern as the Containers create modal: renders the
// collapsible controls and folds values into the exec request body.
const EXEC_OPTION_FIELDS: ExecOptionField[] = [
  { key: 'detach', label: 'Detach (run in background)', kind: 'bool' },
  { key: 'user', label: 'User', kind: 'text', placeholder: 'e.g. root' },
  { key: 'workdir', label: 'Workdir', kind: 'text', placeholder: 'e.g. /app' },
  { key: 'ulimit', label: 'Ulimits', kind: 'repeat', placeholder: 'nofile=1024 (comma/newline separated)' },
]

/** Fold registry values into an exec-body dict; empty values are omitted. */
function foldExecFields(vals: Record<string, string | boolean>): Partial<ExecRequest> {
  const out: Record<string, unknown> = {}
  for (const f of EXEC_OPTION_FIELDS) {
    const v = vals[f.key]
    if (f.kind === 'bool') {
      if (v === true) out[f.key] = true
    } else if (f.kind === 'repeat') {
      const list = String(v ?? '').split(/[,\n]/).map(s => s.trim()).filter(Boolean)
      if (list.length) out[f.key] = list
    } else {
      const s = String(v ?? '').trim()
      if (s) out[f.key] = s
    }
  }
  return out as Partial<ExecRequest>
}

export default function TerminalView({ containerId, onClose }: TerminalViewProps) {
  const termRef = useRef<HTMLDivElement>(null)
  const term = useRef<XTerm | null>(null)
  const fitAddon = useRef<FitAddon | null>(null)
  const [sending, setSending] = useState(false)
  const lineBuf = useRef('')
  const history = useRef<string[]>([])
  const historyIdx = useRef(0)
  const [showCopy, setShowCopy] = useState(false)
  const [direction, setDirection] = useState<CopyDirection>('from')
  const [srcPath, setSrcPath] = useState('')
  const [dstPath, setDstPath] = useState('')
  const [copyResult, setCopyResult] = useState<string | null>(null)
  const copy = useCopyFile()
  const [showExec, setShowExec] = useState(false)
  const [execVals, setExecVals] = useState<Record<string, string | boolean>>({})

  const send = useCallback(async (cmd: string) => {
    if (cmd === 'exit' || cmd === 'logout') {
      term.current?.writeln('\r\n\x1b[33mClosing terminal...\x1b[0m')
      setTimeout(() => onClose?.(), 300)
      return
    }

    setSending(true)
    // Auto-wrap interactive commands to batch/non-interactive mode
    let execCmd: string
    if (cmd === 'top' || cmd === 'htop') {
      execCmd = 'top -bn1 2>/dev/null || top -n 1 -b 2>/dev/null || echo "top not available"'
    } else {
      execCmd = cmd
    }
    try {
      const data = await execContainer(containerId, {
        command: ['sh', '-c', execCmd],
        timeout: 10,
        ...foldExecFields(execVals),
      })
      if (data.stdout) {
        for (const line of data.stdout.split('\n')) {
          term.current?.writeln('\r' + line)
        }
      }
      if (data.stderr) {
        for (const line of data.stderr.split('\n')) {
          if (line.trim()) term.current?.writeln('\r\x1b[31m' + line + '\x1b[0m')
        }
      }
      if (data.exit_code !== 0 && data.exit_code !== undefined) {
        term.current?.writeln(`\r\x1b[33m[exit ${data.exit_code}]\x1b[0m`)
      }
    } catch (err) {
      term.current?.writeln(`\r\n\x1b[31m${err}\x1b[0m`)
    } finally {
      setSending(false)
      term.current?.write('\r\n' + PROMPT)
      lineBuf.current = ''
    }
  }, [containerId, onClose, execVals])

  useEffect(() => {
    const t = new XTerm({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#0a0a0f', foreground: '#d4d4d8', cursor: '#3b82f6',
        selectionBackground: '#3b82f640', black: '#18181b', red: '#ef4444',
        green: '#22c55e', yellow: '#eab308', blue: '#3b82f6',
        magenta: '#a855f7', cyan: '#06b6d4', white: '#d4d4d8',
      },
    })

    const fit = new FitAddon()
    t.loadAddon(fit)
    fitAddon.current = fit

    try { const wg = new WebglAddon(); t.loadAddon(wg); wg.onContextLoss(() => wg.dispose()) } catch { /* fallback */ }

    if (termRef.current) { t.open(termRef.current); fit.fit() }

    t.writeln('\x1b[36mConnected to \x1b[33m' + containerId.slice(0, 12) + '\x1b[0m')
    t.writeln('Type commands, Enter to run. \x1b[33mexit\x1b[0m to close. ↑ for history.')
    t.write('\r\n' + PROMPT)

    term.current = t

    t.onData((data) => {
      if (sending) return

      if (data === '\r') {
        t.write('\r\n')
        const cmd = lineBuf.current.trim()
        if (cmd) {
          history.current.push(cmd)
          historyIdx.current = history.current.length
          send(cmd)
        } else {
          lineBuf.current = ''
          t.write(PROMPT)
        }
        return
      }

      if (data === '\x03' || data === '\x04') {
        t.write('^C\r\n' + PROMPT)
        lineBuf.current = ''
        historyIdx.current = history.current.length
        return
      }

      if (data === '\x7f') {
        if (lineBuf.current.length > 0) {
          lineBuf.current = lineBuf.current.slice(0, -1)
          t.write('\b \b')
        }
        return
      }

      if (data === '\x1b[A') {
        if (history.current.length > 0 && historyIdx.current > 0) {
          historyIdx.current--
          t.write('\r\x1b[K' + PROMPT + history.current[historyIdx.current])
          lineBuf.current = history.current[historyIdx.current]
        }
        return
      }

      if (data === '\x1b[B') {
        t.write('\r\x1b[K')
        if (historyIdx.current < history.current.length - 1) {
          historyIdx.current++
          lineBuf.current = history.current[historyIdx.current]
          t.write(PROMPT + history.current[historyIdx.current])
        } else {
          historyIdx.current = history.current.length
          lineBuf.current = ''
          t.write(PROMPT)
        }
        return
      }

      if (data.length === 1 && data.charCodeAt(0) >= 0x20) {
        lineBuf.current += data
        t.write(data)
      }
    })

    const handleResize = () => fit.fit()
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      t.dispose()
    }
  }, [containerId, send])

  return (
    <Box>
      <GlassBox style={{ marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>☿ Terminal</span>
          <span style={{ fontSize: 12, color: 'var(--muted-fg)', fontFamily: 'monospace', letterSpacing: 1 }}>
            {containerId.slice(0, 12)}
          </span>
          <span style={{ fontSize: 11, color: '#22c55e' }}>● connected</span>
        </div>
        {onClose && (
          <button onClick={onClose} style={{
            background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--fg)', borderRadius: 6, padding: '4px 12px',
            cursor: 'pointer', fontSize: 12,
          }}>
            Close
          </button>
        )}
      </GlassBox>
      <div ref={termRef} style={{
        width: '100%', height: 'calc(100vh - 200px)', minHeight: 400,
        borderRadius: 8, overflow: 'hidden',
      }} />

      <GlassBox style={{ marginTop: 12, padding: 12 }}>
        <button
          onClick={() => setShowExec(!showExec)}
          style={{ background: 'transparent', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 12, padding: 0, fontWeight: 500 }}
        >
          {showExec ? '▾ Hide exec options' : '▸ Exec options'}
        </button>
        {showExec && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12 }}>
            {EXEC_OPTION_FIELDS.map(f => f.kind === 'bool' ? (
              <label key={f.key} style={{ fontSize: 11, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <input type="checkbox" checked={execVals[f.key] === true} onChange={e => setExecVals(v => ({ ...v, [f.key]: e.target.checked }))} />
                {f.label}
              </label>
            ) : (
              <label key={f.key} style={{ fontSize: 11, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                {f.label}
                <input value={String(execVals[f.key] ?? '')} onChange={e => setExecVals(v => ({ ...v, [f.key]: e.target.value }))} placeholder={f.placeholder} style={inputStyle} />
              </label>
            ))}
          </div>
        )}
      </GlassBox>

      <GlassBox style={{ marginTop: 12, padding: 12 }}>
        <button
          onClick={() => setShowCopy(!showCopy)}
          style={{ background: 'transparent', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 12, padding: 0, fontWeight: 500 }}
        >
          {showCopy ? '▾ Hide file copy' : '▸ File copy'}
        </button>
        {showCopy && (
          <form
            onSubmit={e => {
              e.preventDefault()
              copy.mutate(
                { id: containerId, source: srcPath.trim(), destination: dstPath.trim(), direction },
                {
                  onSuccess: r => setCopyResult(r.path ? `Copied — ${r.path}` : 'Copied into container'),
                  onError: err => setCopyResult((err as Error).message),
                },
              )
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}
          >
            <label style={{ fontSize: 11, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }}>
              Direction
              <select value={direction} onChange={e => setDirection(e.target.value as CopyDirection)} style={selectStyle}>
                <option value="from">From container → host</option>
                <option value="to">Host → into container</option>
              </select>
            </label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input
                value={srcPath}
                onChange={e => setSrcPath(e.target.value)}
                placeholder={direction === 'from' ? 'Container path (e.g. /etc/os-release)' : 'Host path (e.g. /tmp/notes.txt)'}
                style={inputStyle}
              />
              <input
                value={dstPath}
                onChange={e => setDstPath(e.target.value)}
                placeholder={direction === 'from' ? 'Host destination path (e.g. /tmp/out.txt)' : 'Container path (e.g. /root/notes.txt)'}
                style={inputStyle}
              />
              <button type="submit" disabled={!srcPath.trim() || !dstPath.trim() || copy.isPending} style={copyBtnStyle}>
                {copy.isPending ? 'Copying...' : 'Copy file'}
              </button>
            </div>
            {copyResult && (
              <p style={{ fontSize: 11, margin: 0, color: copy.isError ? 'var(--danger)' : '#22c55e', wordBreak: 'break-all' }}>
                {copyResult}
              </p>
            )}
          </form>
        )}
      </GlassBox>
    </Box>
  )
}

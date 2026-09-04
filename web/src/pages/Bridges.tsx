import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { type Bridge, getBridges, pairBridge, deleteBridge } from '@/lib/api'

export function Bridges() {
  const [bridges, setBridges] = useState<Bridge[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [pairHost, setPairHost] = useState('')
  const [pairName, setPairName] = useState('Hue Bridge')
  const [pairing, setPairing] = useState(false)
  const [pairError, setPairError] = useState<string | null>(null)
  const [pairSuccess, setPairSuccess] = useState(false)

  async function load() {
    try {
      const data = await getBridges()
      setBridges(data)
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load bridges')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handlePair() {
    if (!pairHost.trim()) return
    setPairing(true)
    setPairError(null)
    setPairSuccess(false)
    try {
      await pairBridge(pairHost.trim(), pairName.trim() || undefined)
      setPairHost('')
      setPairSuccess(true)
      await load()
    } catch (e) {
      setPairError(e instanceof Error ? e.message : 'Pairing failed')
    } finally {
      setPairing(false)
    }
  }

  async function handleDelete(id: string) {
    await deleteBridge(id)
    await load()
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <h2 className="text-sm font-semibold">Pair a bridge</h2>
        <p className="text-xs text-muted-foreground">
          Press the link button on the bridge, then click Pair (times out after 30 s).
        </p>
        <div className="flex gap-2">
          <Input
            placeholder="192.168.1.x"
            value={pairHost}
            onChange={(e) => setPairHost(e.target.value)}
            className="flex-1"
          />
          <Input
            placeholder="Hue Bridge"
            value={pairName}
            onChange={(e) => setPairName(e.target.value)}
            className="w-36"
          />
          <Button onClick={handlePair} disabled={pairing || !pairHost.trim()}>
            {pairing ? 'Pairing…' : 'Pair'}
          </Button>
        </div>
        {pairError && <p className="text-destructive text-sm">{pairError}</p>}
        {pairSuccess && <p className="text-sm text-green-600">Bridge paired successfully.</p>}
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold">Paired bridges</h2>
        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {loadError && <p className="text-destructive text-sm">{loadError}</p>}
        {!loading && !loadError && bridges.length === 0 && (
          <p className="text-sm text-muted-foreground">No bridges paired yet.</p>
        )}
        {bridges.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Host</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {bridges.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-medium">{b.name}</TableCell>
                  <TableCell className="font-mono text-sm text-muted-foreground">{b.host}</TableCell>
                  <TableCell className="text-right">
                    <ConfirmDialog
                      trigger={
                        <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive">
                          Delete
                        </Button>
                      }
                      title="Remove bridge"
                      description={`Remove "${b.name}"? Profiles using this bridge will stop working.`}
                      onConfirm={() => handleDelete(b.id)}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}

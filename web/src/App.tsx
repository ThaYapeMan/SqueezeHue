import { usePreviewSocket } from '@/hooks/usePreviewSocket'
import { NowPlaying } from '@/pages/NowPlaying'
import { Badge } from '@/components/ui/badge'

function ConnectionBadge({ connected, attempt }: { connected: boolean; attempt: number }) {
  if (connected) {
    return <Badge className="text-xs">● Live</Badge>
  }
  return (
    <Badge variant="destructive" className="text-xs">
      {attempt === 0 ? '● Connecting…' : `● Reconnecting (${attempt})…`}
    </Badge>
  )
}

export default function App() {
  const { colour, onset, bars, status, connected, reconnectAttempt } = usePreviewSocket()

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center justify-between">
          <h1 className="text-lg font-semibold tracking-tight">HueSync</h1>
          <ConnectionBadge connected={connected} attempt={reconnectAttempt} />
        </div>
      </header>

      <nav className="border-b border-border">
        <div className="max-w-2xl mx-auto px-4">
          <button className="py-3 text-sm font-medium border-b-2 border-primary text-primary -mb-px">
            Now Playing
          </button>
        </div>
      </nav>

      <main className="max-w-2xl mx-auto px-4 py-6">
        <NowPlaying colour={colour} onset={onset} bars={bars} status={status} />
      </main>
    </div>
  )
}

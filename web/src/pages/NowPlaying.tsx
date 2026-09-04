import { ColourSwatch } from '@/components/ColourSwatch'
import { SpectrumBars } from '@/components/SpectrumBars'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { PreviewState, SocketStatus } from '@/hooks/usePreviewSocket'

function ProcessBadge({ running }: { running: boolean }) {
  return (
    <Badge variant={running ? 'default' : 'destructive'} className="text-xs">
      {running ? 'Running' : 'Stopped'}
    </Badge>
  )
}

function StatusRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </>
  )
}

function StatusGrid({ status }: { status: SocketStatus | null }) {
  if (!status) {
    return <p className="text-sm text-muted-foreground">Waiting for data…</p>
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-2 text-sm items-center">
      <StatusRow label="Profile">
        <span className="font-medium">
          {status.active_profile_id ?? <span className="text-muted-foreground">None</span>}
        </span>
      </StatusRow>

      <StatusRow label="Sync master">
        <code className="text-xs font-mono">
          {status.sync_master ?? <span className="text-muted-foreground not-italic font-sans">—</span>}
        </code>
      </StatusRow>

      <StatusRow label="Delay">
        {status.applied_delay_ms} ms
      </StatusRow>

      <StatusRow label="Bridge">
        <Badge variant={status.bridge_connected ? 'default' : 'secondary'} className="text-xs">
          {status.bridge_connected ? 'Connected' : 'Disconnected'}
        </Badge>
      </StatusRow>

      <StatusRow label="squeezelite">
        <ProcessBadge running={status.processes.squeezelite} />
      </StatusRow>

      <StatusRow label="cava">
        <ProcessBadge running={status.processes.cava} />
      </StatusRow>

      {status.latency_warning && (
        <StatusRow label="Warning">
          <span className="text-destructive text-xs">{status.latency_warning}</span>
        </StatusRow>
      )}
    </dl>
  )
}

type Props = Pick<PreviewState, 'colour' | 'onset' | 'bars' | 'status'>

export function NowPlaying({ colour, onset, bars, status }: Props) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Live preview
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ColourSwatch r={colour.r} g={colour.g} b={colour.b} onset={onset} />
          <p className="text-xs text-muted-foreground mt-2">
            Colour of the first light channel. White outline&nbsp;= onset detected.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Spectrum
          </CardTitle>
        </CardHeader>
        <CardContent>
          <SpectrumBars bars={bars} colorMode={status?.color_mode ?? null} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-xs text-muted-foreground uppercase tracking-wider">
            Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          <StatusGrid status={status} />
        </CardContent>
      </Card>
    </div>
  )
}

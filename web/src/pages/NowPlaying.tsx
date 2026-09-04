import { useEffect, useState } from 'react'
import { ColourSwatch } from '@/components/ColourSwatch'
import { SpectrumBars } from '@/components/SpectrumBars'
import { SliderField } from '@/components/SliderField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import type { PreviewState, SocketStatus } from '@/hooks/usePreviewSocket'
import { restartCava } from '@/lib/api'

const LOG_MIN = Math.log10(20)
const LOG_MAX = Math.log10(20000)

function hzToSlider(hz: number): number {
  return (Math.log10(Math.max(hz, 20)) - LOG_MIN) / (LOG_MAX - LOG_MIN) * 100
}

function sliderToHz(v: number): number {
  return Math.round(10 ** (LOG_MIN + v / 100 * (LOG_MAX - LOG_MIN)))
}

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
    <dl className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-2 text-sm items-start">
      <StatusRow label="Profile">
        {status.active_profile_name ? (
          <span className="font-medium">{status.active_profile_name}</span>
        ) : (
          <span className="text-muted-foreground">None</span>
        )}
      </StatusRow>

      <StatusRow label="Sync master">
        {status.sync_master ? (
          <div>
            {status.sync_master_name && (
              <div className="font-medium">{status.sync_master_name}</div>
            )}
            <code className="text-xs font-mono text-muted-foreground">
              {status.sync_master}
            </code>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
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
  const profileId = status?.active_profile_id ?? null

  // Slider values track the stored cutoff values for the active profile.
  // Reset only when the active profile changes, not on every status tick,
  // so the user's in-progress adjustments are preserved until Apply.
  const [lowSlider, setLowSlider] = useState<number>(50)
  const [highSlider, setHighSlider] = useState<number>(80)
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<string | null>(null)
  const [applyError, setApplyError] = useState(false)

  useEffect(() => {
    if (status?.lower_cutoff_freq != null) {
      setLowSlider(hzToSlider(status.lower_cutoff_freq))
    }
    if (status?.higher_cutoff_freq != null) {
      setHighSlider(hzToSlider(status.higher_cutoff_freq))
    }
    setApplyResult(null)
  }, [profileId])  // reset only on profile switch, not every WebSocket tick

  async function handleApply() {
    if (!profileId) return
    setApplying(true)
    setApplyResult(null)
    setApplyError(false)
    const lowerHz = sliderToHz(lowSlider)
    const higherHz = sliderToHz(highSlider)
    try {
      await restartCava(profileId, { lower_cutoff_freq: lowerHz, higher_cutoff_freq: higherHz })
      setApplyResult('Applied.')
    } catch (e) {
      setApplyResult(e instanceof Error ? e.message : 'Failed')
      setApplyError(true)
    } finally {
      setApplying(false)
    }
  }

  const lowerHz  = sliderToHz(lowSlider)
  const higherHz = sliderToHz(highSlider)

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
        <CardContent className="space-y-4">
          <SpectrumBars
            bars={bars}
            colorMode={status?.color_mode ?? null}
            lowerCutoffHz={lowerHz}
            higherCutoffHz={higherHz}
          />

          {profileId && (
            <>
              <Separator />
              <div className="space-y-3">
                <SliderField
                  label="Low cut"
                  value={lowSlider}
                  min={0}
                  max={100}
                  step={1}
                  format={() => `${lowerHz} Hz`}
                  onChange={setLowSlider}
                  disabled={applying}
                />
                <SliderField
                  label="High cut"
                  value={highSlider}
                  min={0}
                  max={100}
                  step={1}
                  format={() => `${higherHz} Hz`}
                  onChange={setHighSlider}
                  disabled={applying}
                />
                <div className="flex items-center gap-3">
                  <Button size="sm" onClick={handleApply} disabled={applying}>
                    {applying ? 'Applying…' : 'Apply'}
                  </Button>
                  {applyResult && (
                    <span className={applyError ? 'text-destructive text-sm' : 'text-sm text-muted-foreground'}>
                      {applyResult}
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground italic ml-auto">
                    cava restarts briefly
                  </span>
                </div>
              </div>
            </>
          )}
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

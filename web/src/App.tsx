import { usePreviewSocket } from '@/hooks/usePreviewSocket'
import { NowPlaying } from '@/pages/NowPlaying'
import { Profiles } from '@/pages/Profiles'
import { Bridges } from '@/pages/Bridges'
import { Latency } from '@/pages/Latency'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

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
          <div className="flex items-center gap-3">
            {status?.version && (
              <span className="text-xs text-muted-foreground font-mono">{status.version}</span>
            )}
            <ConnectionBadge connected={connected} attempt={reconnectAttempt} />
          </div>
        </div>
      </header>

      <Tabs defaultValue="now-playing">
        <div className="border-b border-border">
          <div className="max-w-2xl mx-auto px-4">
            <TabsList className="h-auto rounded-none bg-transparent p-0 gap-0">
              <TabsTrigger
                value="now-playing"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none py-3 px-4"
              >
                Now Playing
              </TabsTrigger>
              <TabsTrigger
                value="profiles"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none py-3 px-4"
              >
                Profiles
              </TabsTrigger>
              <TabsTrigger
                value="bridges"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none py-3 px-4"
              >
                Bridges
              </TabsTrigger>
              <TabsTrigger
                value="latency"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none py-3 px-4"
              >
                Latency
              </TabsTrigger>
            </TabsList>
          </div>
        </div>

        <div className="max-w-2xl mx-auto px-4 py-6">
          <TabsContent value="now-playing">
            <NowPlaying colour={colour} onset={onset} bars={bars} status={status} />
          </TabsContent>

          <TabsContent value="profiles">
            <Profiles
              activeProfileId={status?.active_profile_id ?? null}
              onActivationChange={() => {}}
            />
          </TabsContent>

          <TabsContent value="bridges">
            <Bridges />
          </TabsContent>

          <TabsContent value="latency">
            <Latency
              syncMaster={status?.sync_master ?? null}
              syncMasterName={status?.sync_master_name ?? null}
            />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  )
}

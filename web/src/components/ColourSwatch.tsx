import { cn } from '@/lib/utils'

interface Props {
  r: number
  g: number
  b: number
  onset: boolean
}

export function ColourSwatch({ r, g, b, onset }: Props) {
  const to255 = (v: number) => Math.round(v * 255)
  return (
    <div
      className={cn(
        'w-full h-20 rounded-lg border border-border',
        'transition-[background-color] duration-75',
        onset && 'outline outline-2 outline-white'
      )}
      style={{ backgroundColor: `rgb(${to255(r)}, ${to255(g)}, ${to255(b)})` }}
    />
  )
}

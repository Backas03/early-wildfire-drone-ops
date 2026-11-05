// 지도 표식 (미션 계획·실시간 관제 공용). 모양은 mission.css의 .wp, .home-pin, .drone 등
import L from 'leaflet'

export interface DroneMarker {
  id: string
  lat: number
  lon: number
  heading: number
  alt: number
  online: boolean
}

export type WpKind = 'wp' | 'loiter' | 'land'

const iconCache = new Map<string, L.DivIcon>()
export function cachedIcon(key: string, make: () => L.DivIcon): L.DivIcon {
  let ic = iconCache.get(key)
  if (!ic) {
    ic = make()
    iconCache.set(key, ic)
  }
  return ic
}

/** 번호 붙은 웨이포인트. extra: 'sel' | 'dim' | 'error' | 'done' | 'target' 조합 */
export function wpIcon(n: number, kind: WpKind, ...extra: (string | false | null | undefined)[]) {
  const cls = ['wp', kind, ...extra.filter(Boolean)].join(' ')
  return cachedIcon(`wp|${n}|${cls}`, () =>
    L.divIcon({ className: 'map-icon', html: `<div class="${cls}">${n}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] }),
  )
}

export const homeIcon = () =>
  cachedIcon('home', () =>
    L.divIcon({ className: 'map-icon', html: '<div class="home-pin">H</div>', iconSize: [26, 26], iconAnchor: [13, 13] }),
  )

export const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

/** 기수 방향으로 돌아간 화살표 + 이름·고도 라벨 (매번 바뀌므로 캐시하지 않음) */
export function droneIcon(d: DroneMarker, selected = false) {
  return L.divIcon({
    className: 'map-icon',
    html:
      `<div class="drone${d.online ? '' : ' off'}${selected ? ' sel' : ''}">` +
      `<svg viewBox="0 0 24 24" style="transform: rotate(${d.heading.toFixed(0)}deg)"><path d="M12 2 L20 21 L12 16.5 L4 21 Z"/></svg>` +
      `<span>${escapeHtml(d.id)} · ${d.alt.toFixed(0)} m</span></div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  })
}

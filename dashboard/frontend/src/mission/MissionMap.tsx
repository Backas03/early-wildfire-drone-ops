import 'leaflet/dist/leaflet.css'

import L from 'leaflet'
import { useEffect, useRef, useState } from 'react'
import { MapContainer, Marker, Polygon, Polyline, Tooltip, ZoomControl, useMap, useMapEvents } from 'react-leaflet'

import { midpoint, type LatLon } from '../lib/geo.ts'
import { itemErrors, mapPosition } from '../lib/missionCheck.ts'
import BaseLayers from './BaseLayers.tsx'
import type { Draft } from './draft.ts'
import { cachedIcon, droneIcon, homeIcon, wpIcon, type DroneMarker } from './mapIcons.ts'

export type MapMode = 'select' | 'add' | 'home' | 'area'
export type { DroneMarker }

interface Props {
  draft: Draft
  selected: number | null
  mode: MapMode
  area: LatLon[]
  gridPreview: LatLon[]
  drones: DroneMarker[]
  /** 값이 바뀔 때마다 미션 전체가 보이게 지도를 맞춘다 */
  fitSignal: number
  initialCenter: LatLon
  onMapClick: (p: LatLon) => void
  onSelect: (index: number) => void
  onMoveItem: (index: number, p: LatLon) => void
  onInsertAt: (index: number, p: LatLon) => void
  onMoveHome: (p: LatLon) => void
  onAreaChange: (area: LatLon[]) => void
}

// 지도 위 색은 지도·위성 배경 위에서 잘 보이도록 테마와 관계없이 고정
const PATH_COLOR = '#ff8c00'
const AREA_COLOR = '#a855f7'
const GRID_COLOR = '#06b6d4'

const ll = (p: LatLon): L.LatLngTuple => [p.lat, p.lon]
const fromLL = (l: L.LatLng): LatLon => ({ lat: l.lat, lon: l.lng })

const plusIcon = (cls: string) =>
  cachedIcon(`plus|${cls}`, () =>
    L.divIcon({ className: 'map-icon', html: `<div class="${cls}">+</div>`, iconSize: [18, 18], iconAnchor: [9, 9] }),
  )
const vertexIcon = () =>
  cachedIcon('vertex', () =>
    L.divIcon({ className: 'map-icon', html: '<div class="area-vertex"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }),
  )

export default function MissionMap(props: Props) {
  return (
    <MapContainer
      center={ll(props.initialCenter)}
      zoom={16}
      maxZoom={21}
      zoomControl={false}
      doubleClickZoom={false}
      className="map"
    >
      <ZoomControl position="bottomright" />
      <BaseLayers />
      <MapContent {...props} />
    </MapContainer>
  )
}

const HOME = -1
const HOME_KEY = '__home__'

function MapContent(p: Props) {
  const map = useMap()
  const [, setZoom] = useState(map.getZoom())
  // 끄는 중인 점 (끝나면 상위로 알림). 끄는 동안 선이 따라오게 하려고 따로 둔다.
  // 항목은 번호가 아니라 key로 가리킨다 (끄는 도중 목록이 바뀌어도 다른 점이 움직이지 않게).
  const [drag, setDrag] = useState<{ key: string; p: LatLon } | null>(null)
  const [vdrag, setVdrag] = useState<{ index: number; p: LatLon } | null>(null)

  useMapEvents({
    click: (e) => p.onMapClick(fromLL(e.latlng)),
    zoomend: () => setZoom(map.getZoom()),
  })

  // 모드별 커서 (MapContainer의 className은 Leaflet 클래스를 덮어쓰므로 data 속성 사용)
  useEffect(() => {
    map.getContainer().dataset.mode = p.mode
  }, [map, p.mode])

  // fitSignal이 바뀔 때만 맞추고, 그때의 최신 미션을 쓴다
  const latest = useRef({ draft: p.draft, area: p.area })
  useEffect(() => {
    latest.current = { draft: p.draft, area: p.area }
  })
  useEffect(() => {
    const { draft, area } = latest.current
    const pts: L.LatLngTuple[] = []
    if (draft.home) pts.push(ll(draft.home))
    for (const it of draft.items) {
      const pos = mapPosition(it)
      if (pos) pts.push(ll(pos))
    }
    for (const v of area) pts.push(ll(v))
    if (pts.length === 1) map.setView(pts[0], Math.max(map.getZoom(), 16))
    else if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 18 })
  }, [map, p.fitSignal])

  // 목록에서 고른 항목이 화면 밖이면 그쪽으로 이동
  const items = p.draft.items
  useEffect(() => {
    if (p.selected == null) return
    const it = items[p.selected]
    const pos = it && mapPosition(it)
    if (pos && !map.getBounds().contains(ll(pos))) map.panTo(ll(pos))
  }, [map, p.selected, items])

  const home = drag?.key === HOME_KEY ? drag.p : p.draft.home
  const posOf = (i: number) => (drag && drag.key === items[i].key ? drag.p : mapPosition(items[i]))
  const endIdx = items.findIndex((it) => it.command === 'rtl' || it.command === 'land')

  // 비행 경로: 홈 → 위치가 있는 항목들 (미션이 끝나는 RTL/착륙까지)
  const path: { p: LatLon; index: number }[] = []
  if (home) path.push({ p: home, index: HOME })
  const last = endIdx >= 0 ? endIdx : items.length - 1
  for (let i = 0; i <= last; i++) {
    const pos = posOf(i)
    if (pos) path.push({ p: pos, index: i })
  }
  const segments: { a: LatLon; b: LatLon; insertAt: number }[] = []
  for (let i = 1; i < path.length; i++) segments.push({ a: path[i - 1].p, b: path[i].p, insertAt: path[i].index })
  const returnLeg =
    endIdx >= 0 && items[endIdx].command === 'rtl' && home && path.length > 1
      ? { a: path[path.length - 1].p, b: home, insertAt: endIdx }
      : null
  if (returnLeg) segments.push(returnLeg)

  // 화면에서 충분히 긴 구간에만 중간 삽입 손잡이(+)를 보인다
  const longOnScreen = (a: LatLon, b: LatLon) =>
    map.latLngToContainerPoint(ll(a)).distanceTo(map.latLngToContainerPoint(ll(b))) > 70
  const showHandles = p.mode === 'select' || p.mode === 'add'

  const area = p.area.map((v, i) => (vdrag?.index === i ? vdrag.p : v))

  return (
    <>
      {area.length >= 2 && (
        <Polygon
          positions={area.map(ll)}
          pathOptions={{ color: AREA_COLOR, weight: 2, dashArray: '6 4', fillOpacity: 0.08 }}
          interactive={false}
        />
      )}
      {p.gridPreview.length > 1 && (
        <Polyline
          positions={p.gridPreview.map(ll)}
          pathOptions={{ color: GRID_COLOR, weight: 2, dashArray: '4 6' }}
          interactive={false}
        />
      )}

      {path.length > 1 && (
        <Polyline positions={path.map((x) => ll(x.p))} pathOptions={{ color: PATH_COLOR, weight: 3 }} interactive={false} />
      )}
      {returnLeg && (
        <Polyline
          positions={[ll(returnLeg.a), ll(returnLeg.b)]}
          pathOptions={{ color: PATH_COLOR, weight: 2, dashArray: '8 6' }}
          interactive={false}
        />
      )}

      {showHandles &&
        segments
          .filter((s) => longOnScreen(s.a, s.b))
          .map((s) => (
            <Marker
              key={`mid-${s.insertAt}-${s.a.lat}-${s.a.lon}`}
              position={ll(midpoint(s.a, s.b))}
              icon={plusIcon('mid-handle')}
              eventHandlers={{ click: () => p.onInsertAt(s.insertAt, midpoint(s.a, s.b)) }}
            >
              <Tooltip direction="top" offset={[0, -8]}>
                여기에 웨이포인트 추가
              </Tooltip>
            </Marker>
          ))}

      {home && (
        <Marker
          position={ll(home)}
          icon={homeIcon()}
          draggable
          zIndexOffset={-100}
          eventHandlers={{
            drag: (e) => setDrag({ key: HOME_KEY, p: fromLL((e.target as L.Marker).getLatLng()) }),
            dragend: (e) => {
              setDrag(null)
              p.onMoveHome(fromLL((e.target as L.Marker).getLatLng()))
            },
          }}
        >
          <Tooltip direction="top" offset={[0, -14]}>
            홈 (이륙 지점)
          </Tooltip>
        </Marker>
      )}

      {items.map((it, i) => {
        const pos = posOf(i)
        if (!pos) return null
        const kind = it.command === 'loiter_time' ? 'loiter' : it.command === 'land' ? 'land' : 'wp'
        return (
          <Marker
            key={it.key}
            position={ll(pos)}
            icon={wpIcon(
              i + 1,
              kind,
              i === p.selected && 'sel',
              endIdx >= 0 && i > endIdx && 'dim',
              itemErrors(it).length > 0 && 'error',
            )}
            draggable
            zIndexOffset={i === p.selected ? 1000 : 0}
            eventHandlers={{
              click: () => p.onSelect(i),
              // 끄는 도중에 아이콘이 바뀌면 Leaflet이 끌기를 끊으므로, 선택(아이콘 색 변경)은 끝난 뒤에 한다
              drag: (e) => setDrag({ key: it.key, p: fromLL((e.target as L.Marker).getLatLng()) }),
              dragend: (e) => {
                setDrag(null)
                p.onMoveItem(i, fromLL((e.target as L.Marker).getLatLng()))
                p.onSelect(i)
              },
            }}
          />
        )
      })}

      {p.mode === 'area' &&
        area.map((v, i) => (
          <Marker
            key={`v-${i}`}
            position={ll(v)}
            icon={vertexIcon()}
            draggable
            zIndexOffset={1500}
            eventHandlers={{
              drag: (e) => setVdrag({ index: i, p: fromLL((e.target as L.Marker).getLatLng()) }),
              dragend: (e) => {
                setVdrag(null)
                const moved = fromLL((e.target as L.Marker).getLatLng())
                p.onAreaChange(p.area.map((q, j) => (j === i ? moved : q)))
              },
              contextmenu: () => p.onAreaChange(p.area.filter((_, j) => j !== i)),
            }}
          >
            <Tooltip direction="top" offset={[0, -8]}>
              끌어서 이동 · 우클릭으로 삭제
            </Tooltip>
          </Marker>
        ))}
      {p.mode === 'area' &&
        area.length >= 3 &&
        area
          .map((v, i) => ({ a: v, b: area[(i + 1) % area.length], at: i + 1 }))
          .filter((s) => longOnScreen(s.a, s.b))
          .map((s) => (
            <Marker
              key={`vm-${s.at}`}
              position={ll(midpoint(s.a, s.b))}
              icon={plusIcon('mid-handle area')}
              zIndexOffset={1400}
              eventHandlers={{
                click: () => {
                  const next = [...p.area]
                  next.splice(s.at, 0, midpoint(s.a, s.b))
                  p.onAreaChange(next)
                },
              }}
            />
          ))}

      {p.drones.map((d) => (
        <Marker key={`drone-${d.id}`} position={[d.lat, d.lon]} icon={droneIcon(d)} interactive={false} zIndexOffset={2000} />
      ))}
    </>
  )
}

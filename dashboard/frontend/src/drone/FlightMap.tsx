import 'leaflet/dist/leaflet.css'

import L from 'leaflet'
import { useEffect, useRef } from 'react'
import { Circle, MapContainer, Marker, Polygon, Polyline, Tooltip, ZoomControl, useMap } from 'react-leaflet'

import type { LatLon } from '../lib/geo.ts'
import { COMMAND_LABEL, mapPosition } from '../lib/missionCheck.ts'
import BaseLayers from '../mission/BaseLayers.tsx'
import { droneIcon, homeIcon, wpIcon, type DroneMarker } from '../mission/mapIcons.ts'
import PlaceSearch from '../mission/PlaceSearch.tsx'
import type { MissionPlan, SimFires } from '../protocol.ts'
import type { TrailPoint } from '../useLiveFeed.ts'

interface Props {
  drones: DroneMarker[]
  selectedId: string | null
  trails: Record<string, TrailPoint[]>
  /** 고른 드론의 FC에 들어 있는 미션 */
  mission: MissionPlan | null
  home: LatLon | null
  /** 고른 드론이 지금 향하는 미션 항목 (MissionPlan seq). 비행 중이 아니면 null */
  currentSeq: number | null
  follow: boolean
  fitSignal: number
  initialCenter: LatLon
  onSelectDrone: (id: string) => void
  /** 고른 드론 카메라가 내려다보는 땅 범위 */
  footprint: LatLon[] | null
  /** 시뮬레이션 가상 화재 정답 위치 */
  simFires: SimFires | null
  /** 장소 검색으로 지도를 옮겼을 때 (따라가기를 꺼야 드론 쪽으로 되돌아가지 않는다) */
  onPlaceJump: () => void
}

const PATH_COLOR = '#ff8c00'
const CAMERA_COLOR = '#22d3ee'
const SIM_FIRE_COLOR = '#ef4444'
const TRAIL_COLOR = '#e11d48'
const OTHER_TRAIL_COLOR = '#94a3b8'

const ll = (p: LatLon): L.LatLngTuple => [p.lat, p.lon]

export default function FlightMap(props: Props) {
  return (
    <MapContainer center={ll(props.initialCenter)} zoom={17} maxZoom={21} zoomControl={false} className="map">
      <ZoomControl position="bottomright" />
      <BaseLayers />
      <PlaceSearch onJump={props.onPlaceJump} />
      <FlightContent {...props} />
    </MapContainer>
  )
}

function FlightContent(p: Props) {
  const map = useMap()
  const items = p.mission?.items ?? []
  const selected = p.drones.find((d) => d.id === p.selectedId) ?? null

  // fitSignal이 바뀌면 미션·홈·드론이 다 보이게
  const latest = useRef({ items, home: p.home, selected })
  useEffect(() => {
    latest.current = { items, home: p.home, selected }
  })
  useEffect(() => {
    const { items, home, selected } = latest.current
    const pts: L.LatLngTuple[] = []
    if (home) pts.push(ll(home))
    if (selected) pts.push([selected.lat, selected.lon])
    for (const it of items) {
      const pos = mapPosition(it)
      if (pos) pts.push(ll(pos))
    }
    if (pts.length === 1) map.setView(pts[0], Math.max(map.getZoom(), 17))
    else if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 18 })
  }, [map, p.fitSignal])

  // 따라가기: 드론이 화면 가운데 근처를 벗어나면 옮긴다
  const lat = selected?.lat
  const lon = selected?.lon
  useEffect(() => {
    if (!p.follow || lat == null || lon == null) return
    const inner = map.getBounds().pad(-0.3)
    if (!inner.contains([lat, lon])) map.panTo([lat, lon], { animate: true, duration: 0.5 })
  }, [map, p.follow, lat, lon])

  // 미션 경로: 홈 → 위치가 있는 항목들 (RTL/착륙까지), RTL이면 홈으로 돌아오는 점선
  const endIdx = items.findIndex((it) => it.command === 'rtl' || it.command === 'land')
  const last = endIdx >= 0 ? endIdx : items.length - 1
  const path: LatLon[] = p.home ? [p.home] : []
  for (let i = 0; i <= last; i++) {
    const pos = mapPosition(items[i])
    if (pos) path.push(pos)
  }
  const returnLeg = endIdx >= 0 && items[endIdx].command === 'rtl' && p.home && path.length > 1

  return (
    <>
      {p.simFires?.fires.map((f) => (
        <Circle
          key={`simfire-${f.fire_id}`}
          center={[f.lat, f.lon]}
          radius={Math.max(f.size_m, 4)}
          pathOptions={{ color: SIM_FIRE_COLOR, weight: 2, dashArray: '4 4', fillOpacity: 0.15 }}
        >
          <Tooltip direction="right" offset={[8, 0]} permanent className="sim-fire-label">
            가상 화재 {f.fire_id}
          </Tooltip>
        </Circle>
      ))}
      {p.footprint && (
        <Polygon
          positions={p.footprint.map(ll)}
          pathOptions={{ color: CAMERA_COLOR, weight: 1.5, dashArray: '5 4', fillOpacity: 0.08 }}
          interactive={false}
        />
      )}
      {path.length > 1 && (
        <Polyline positions={path.map(ll)} pathOptions={{ color: PATH_COLOR, weight: 3, opacity: 0.85 }} interactive={false} />
      )}
      {returnLeg && (
        <Polyline
          positions={[ll(path[path.length - 1]), ll(p.home!)]}
          pathOptions={{ color: PATH_COLOR, weight: 2, dashArray: '8 6' }}
          interactive={false}
        />
      )}

      {p.home && (
        <Marker position={ll(p.home)} icon={homeIcon()} zIndexOffset={-100}>
          <Tooltip direction="top" offset={[0, -14]}>
            홈 (드론이 시동 건 곳)
          </Tooltip>
        </Marker>
      )}

      {items.map((it, i) => {
        const pos = mapPosition(it)
        if (!pos) return null
        const kind = it.command === 'loiter_time' ? 'loiter' : it.command === 'land' ? 'land' : 'wp'
        const cur = p.currentSeq
        return (
          <Marker
            key={`${i}-${pos.lat}-${pos.lon}`}
            position={ll(pos)}
            icon={wpIcon(i + 1, kind, cur != null && i < cur && 'done', cur === i && 'target')}
            zIndexOffset={cur === i ? 500 : 0}
          >
            <Tooltip direction="top" offset={[0, -12]}>
              {i + 1}번 {COMMAND_LABEL[it.command]}
              {it.alt != null && ` · ${it.alt} m`}
              {it.hold_s > 0 && ` · ${it.hold_s}초`}
            </Tooltip>
          </Marker>
        )
      })}

      {Object.entries(p.trails).map(([id, trail]) =>
        trail.length > 1 ? (
          <Polyline
            key={`trail-${id}`}
            positions={trail}
            pathOptions={{
              color: id === p.selectedId ? TRAIL_COLOR : OTHER_TRAIL_COLOR,
              weight: id === p.selectedId ? 3 : 2,
              opacity: 0.9,
            }}
            interactive={false}
          />
        ) : null,
      )}

      {p.drones.map((d) => (
        <Marker
          key={`drone-${d.id}`}
          position={[d.lat, d.lon]}
          icon={droneIcon(d, d.id === p.selectedId)}
          zIndexOffset={d.id === p.selectedId ? 3000 : 2000}
          eventHandlers={{ click: () => p.onSelectDrone(d.id) }}
        />
      ))}
    </>
  )
}

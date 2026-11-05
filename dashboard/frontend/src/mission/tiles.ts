// 지도 타일 설정.
// VWorld(국토교통부) 키가 있으면 VWorld 지도·위성사진을 쓰고, 없으면 OpenStreetMap과 Esri 위성사진을 쓴다.
// 키 발급: https://www.vworld.kr → 오픈API → 인증키 발급 (서비스 URL에 http://localhost:5173 등록)
// 키 설정: dashboard/frontend/.env.local 에  VITE_VWORLD_KEY=발급받은키

export interface TileDef {
  name: string
  url: string
  attribution: string
  maxNativeZoom: number
  minZoom?: number
}

const key = (import.meta.env.VITE_VWORLD_KEY as string | undefined)?.trim()

export const usingVworld = Boolean(key)

const VWORLD_ATTR = '&copy; <a href="https://www.vworld.kr">VWorld</a> 국토교통부'

export const baseLayers: TileDef[] = key
  ? [
      {
        name: 'VWorld 일반',
        url: `https://api.vworld.kr/req/wmts/1.0.0/${key}/Base/{z}/{y}/{x}.png`,
        attribution: VWORLD_ATTR,
        maxNativeZoom: 19,
        minZoom: 6,
      },
      {
        name: 'VWorld 위성',
        url: `https://api.vworld.kr/req/wmts/1.0.0/${key}/Satellite/{z}/{y}/{x}.jpeg`,
        attribution: VWORLD_ATTR,
        maxNativeZoom: 19,
        minZoom: 6,
      },
    ]
  : [
      {
        name: 'OpenStreetMap',
        url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxNativeZoom: 19,
      },
      {
        name: '위성 (Esri)',
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri',
        maxNativeZoom: 19,
      },
    ]

/** 위성사진 위에 겹치는 지명·도로 (VWorld만) */
export const labelOverlay: TileDef | null = key
  ? {
      name: '지명·도로',
      url: `https://api.vworld.kr/req/wmts/1.0.0/${key}/Hybrid/{z}/{y}/{x}.png`,
      attribution: VWORLD_ATTR,
      maxNativeZoom: 19,
      minZoom: 6,
    }
  : null

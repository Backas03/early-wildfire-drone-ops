import { LayersControl, TileLayer } from 'react-leaflet'

import { baseLayers, labelOverlay } from './tiles.ts'

/** 배경 지도 선택 (VWorld 또는 OSM/Esri). MapContainer 안에 넣는다. */
export default function BaseLayers() {
  return (
    <LayersControl position="topright">
      {baseLayers.map((t, i) => (
        <LayersControl.BaseLayer key={t.name} name={t.name} checked={i === 0}>
          <TileLayer url={t.url} attribution={t.attribution} maxNativeZoom={t.maxNativeZoom} maxZoom={21} minZoom={t.minZoom} />
        </LayersControl.BaseLayer>
      ))}
      {labelOverlay && (
        <LayersControl.Overlay name={labelOverlay.name}>
          <TileLayer
            url={labelOverlay.url}
            attribution={labelOverlay.attribution}
            maxNativeZoom={labelOverlay.maxNativeZoom}
            maxZoom={21}
            minZoom={labelOverlay.minZoom}
          />
        </LayersControl.Overlay>
      )}
    </LayersControl>
  )
}

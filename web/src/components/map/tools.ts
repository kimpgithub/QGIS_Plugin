// 4개 툴(라인등록/라인삭제/삭제표기/속성등록) 의 OpenLayers Draw interaction
// 어태치. 각 툴은 그리기 종료(drawend) 시 onComplete 콜백에 GeoJSON Geometry 를
// 전달. 호출부가 그 데이터로 저장/취소 모달을 띄운다.
import Map from 'ol/Map';
import Draw from 'ol/interaction/Draw';
import VectorSource from 'ol/source/Vector';
import VectorLayer from 'ol/layer/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { Style, Stroke, Fill, Circle as CircleStyle } from 'ol/style';
import type { Feature } from 'ol';
import type { MarkupKind, GjGeometry } from '../../types';

const KIND_TO_OL: Record<MarkupKind, 'LineString' | 'Point'> = {
  add: 'LineString',
  delete: 'LineString',
  attr: 'Point',
  delete_mark: 'Point',
};

const KIND_STYLE: Record<MarkupKind, Style> = {
  add: new Style({ stroke: new Stroke({ color: '#1d4ed8', width: 3 }) }),
  delete: new Style({ stroke: new Stroke({ color: '#dc2626', width: 4 }) }),
  attr: new Style({
    image: new CircleStyle({
      radius: 7,
      fill: new Fill({ color: '#1d4ed8' }),
      stroke: new Stroke({ color: '#fff', width: 2 }),
    }),
  }),
  delete_mark: new Style({
    image: new CircleStyle({
      radius: 8,
      fill: new Fill({ color: 'rgba(220,38,38,0.85)' }),
      stroke: new Stroke({ color: '#fff', width: 2 }),
    }),
  }),
};

export type ActiveTool = {
  kind: MarkupKind;
  draw: Draw;
  layer: VectorLayer<VectorSource>;
  source: VectorSource;
  detach: () => void;
};

export function attachTool(
  map: Map,
  kind: MarkupKind,
  onComplete: (geom: GjGeometry) => void
): ActiveTool {
  const source = new VectorSource();
  const layer = new VectorLayer({
    source,
    style: KIND_STYLE[kind],
  });
  map.addLayer(layer);

  const draw = new Draw({
    source,
    type: KIND_TO_OL[kind],
    // 라인등록은 더블클릭으로 종료 (slide 4 명세) — OL 기본 동작
  });
  map.addInteraction(draw);

  draw.on('drawend', (e: { feature: Feature }) => {
    const fmt = new GeoJSON();
    const obj = fmt.writeGeometryObject(e.feature.getGeometry()!, {
      featureProjection: 'EPSG:3857',
      dataProjection: 'EPSG:4326',
    }) as GjGeometry;
    onComplete(obj);
  });

  return {
    kind,
    draw,
    layer,
    source,
    detach: () => {
      map.removeInteraction(draw);
      map.removeLayer(layer);
    },
  };
}

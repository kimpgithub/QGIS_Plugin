// 4개 툴(라인등록/라인삭제/삭제표기/속성등록) 의 OpenLayers Draw interaction
// 어태치. 각 툴은 그리기 종료(drawend) 시 onComplete 콜백에 GeoJSON Geometry 를
// 전달. 호출부가 그 데이터로 저장/취소 모달을 띄운다.
import Map from 'ol/Map';
import Draw from 'ol/interaction/Draw';
import Snap from 'ol/interaction/Snap';
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
  // 삭제표기: 경계선을 따라 그은 구간(LineString) — 그릴 때 boundary 에 스냅.
  delete_mark: 'LineString',
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
  // 그리는 중 표시 — 빨간 점선(완료 후 ✕ 반복은 MapView.styleMarkup 가 담당).
  delete_mark: new Style({
    stroke: new Stroke({ color: '#dc2626', width: 3, lineDash: [6, 6] }),
  }),
};

export type ActiveTool = {
  kind: MarkupKind;
  draw: Draw;
  layer: VectorLayer<VectorSource>;
  source: VectorSource;
  isLine: boolean;              // add/delete 만 다점(라인) — 점 취소가 의미 있음
  removeLastPoint: () => void;  // 그리는 중 마지막 꼭짓점만 되돌림
  abort: () => void;            // 그리던 도형 전체 취소 (drawabort)
  detach: () => void;
};

export function attachTool(
  map: Map,
  kind: MarkupKind,
  onComplete: (geom: GjGeometry) => void,
  // 그리기 진행 상태 변화 통지 — true=그리는 중, false=종료/취소.
  // 호출부가 안내바 버튼 활성/비활성에 사용.
  onDrawingChange?: (drawing: boolean) => void,
  // 스냅 대상 소스 — 삭제표기(delete_mark)는 boundary 에 스냅해 경계선을 따라 그림.
  snapSource?: VectorSource | null
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

  // 스냅은 Draw 보다 뒤에 추가해야 우선 적용됨(OL 권장).
  const snap = snapSource ? new Snap({ source: snapSource }) : null;
  if (snap) map.addInteraction(snap);

  draw.on('drawstart', () => onDrawingChange?.(true));

  draw.on('drawend', (e: { feature: Feature }) => {
    onDrawingChange?.(false);
    const fmt = new GeoJSON();
    const obj = fmt.writeGeometryObject(e.feature.getGeometry()!, {
      featureProjection: 'EPSG:3857',
      dataProjection: 'EPSG:4326',
    }) as GjGeometry;
    onComplete(obj);
  });

  // abortDrawing() 또는 외부 요인으로 그리기가 취소될 때.
  draw.on('drawabort', () => onDrawingChange?.(false));

  const isLine = kind === 'add' || kind === 'delete' || kind === 'delete_mark';

  return {
    kind,
    draw,
    layer,
    source,
    isLine,
    removeLastPoint: () => {
      // 점이 하나뿐이면 removeLastPoint 가 그리기를 끝내지 못하므로 전체 취소.
      if (isLine) draw.removeLastPoint();
      else draw.abortDrawing();
    },
    abort: () => draw.abortDrawing(),
    detach: () => {
      map.removeInteraction(draw);
      if (snap) map.removeInteraction(snap);
      map.removeLayer(layer);
    },
  };
}

import { useEffect, useRef } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { Style, Stroke, Circle as CircleStyle, Fill } from 'ol/style';
import { transformExtent } from 'ol/proj';
import 'ol/ol.css';
import type { GjFeatureCollection, MarkupCollection } from '../../types';
import type { AdminOutlineCollection } from '../../api/admin_outline';

const VWORLD_KEY =
  import.meta.env.VITE_VWORLD_KEY || '55A24471-C374-3A22-8652-6E8D55D53E08';

// 좌측 범례 체크박스가 토글할 레이어 id ('base' = vworld 배경지도)
export type LayerKey = 'base' | 'cog' | 'admin' | 'ri' | 'markup';

export type LayerVisibility = Record<LayerKey, boolean>;

export type MapHandle = {
  fitToBoundary: () => void;
  getMap: () => Map | null;
};

type Props = {
  cogTileUrl?: string | null;        // COG 베이스 타일 URL 템플릿
  cogBbox?: [number, number, number, number] | null;  // [minLon,minLat,maxLon,maxLat] EPSG:4326
  adminOutline?: AdminOutlineCollection | null;  // 선택 읍면 + 주변 buffer
  boundary?: GjFeatureCollection | null;         // 행정리경계
  markup?: MarkupCollection | null;              // 수정요청 레이어
  visible: LayerVisibility;
  onMapReady?: (handle: MapHandle) => void;
};

const KOREA_EXTENT = transformExtent(
  [124.8, 33.0, 130.0, 38.8],
  'EPSG:4326',
  'EPSG:3857'
);

export default function MapView({
  cogTileUrl,
  cogBbox,
  adminOutline,
  boundary,
  markup,
  visible,
  onMapReady,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const baseRef = useRef<TileLayer<XYZ> | null>(null);
  const cogRef = useRef<TileLayer<XYZ> | null>(null);
  const boundarySrcRef = useRef<VectorSource | null>(null);
  const markupSrcRef = useRef<VectorSource | null>(null);
  const boundaryLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const markupLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const adminLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const adminSrcRef = useRef<VectorSource | null>(null);
  const cogBboxRef = useRef<[number, number, number, number] | null>(null);

  // map init (once)
  useEffect(() => {
    if (!ref.current) return;

    const view = new View({ projection: 'EPSG:3857' });

    // 배경지도 (vworld Base) — 기본은 백지(off). 좌측 레이어에서 토글.
    // transition:0 — 확대/축소 시 타일 페이드인 깜빡임 제거(OL 기본 250ms 페이드).
    const base = new TileLayer<XYZ>({
      source: new XYZ({
        url: `/vworld/req/wmts/1.0.0/${VWORLD_KEY}/Base/{z}/{y}/{x}.png`,
        transition: 0,
      }),
      visible: false,
    });
    baseRef.current = base;

    // COG 베이스 — cogTileUrl 주어지면 source 갱신
    const cog = new TileLayer<XYZ>({ visible: false, opacity: 0.9 });
    cogRef.current = cog;

    // 행정읍면 라인 — 저장소 SHP (bnd_adm_pg) 적재본 (admin_outline). 선택된 읍면 +
    // 1km buffer 만 InspectPage 에서 fetch 해 prop 으로 내려준다.
    // is_target=true 폴리곤은 strong, 이웃은 thin gray.
    const adminSrc = new VectorSource();
    adminSrcRef.current = adminSrc;
    const admin = new VectorLayer({
      source: adminSrc,
      style: (feat) => {
        const isTarget = feat.get('is_target') === true;
        return new Style({
          stroke: new Stroke({
            color: isTarget ? '#111827' : '#6b7280',
            width: isTarget ? 1.6 : 0.8,
          }),
        });
      },
    });
    adminLayerRef.current = admin;

    const boundarySrc = new VectorSource();
    boundarySrcRef.current = boundarySrc;
    const boundaryLayer = new VectorLayer({
      source: boundarySrc,
      style: new Style({
        stroke: new Stroke({ color: '#f59e0b', width: 2 }),
        fill: new Fill({ color: 'rgba(245,158,11,0.04)' }),
      }),
    });
    boundaryLayerRef.current = boundaryLayer;

    const markupSrc = new VectorSource();
    markupSrcRef.current = markupSrc;
    const markupLayer = new VectorLayer({
      source: markupSrc,
      style: styleMarkup,
    });
    markupLayerRef.current = markupLayer;

    const map = new Map({
      target: ref.current,
      layers: [base, cog, admin, boundaryLayer, markupLayer],
      view,
    });
    mapRef.current = map;

    map.updateSize();
    view.fit(KOREA_EXTENT, { size: map.getSize(), padding: [20, 20, 20, 20] });

    const onResize = () => map.updateSize();
    window.addEventListener('resize', onResize);

    const handle: MapHandle = {
      getMap: () => mapRef.current,
      fitToBoundary: () => {
        const m = mapRef.current;
        if (!m) return;
        const opts = { padding: [40, 40, 40, 40], duration: 400 };
        const src = boundarySrcRef.current;
        const bExt = src?.getExtent();
        if (bExt && isFinite(bExt[0])) {
          m.getView().fit(bExt, opts);
          return;
        }
        // fallback: COG bbox
        const bb = cogBboxRef.current;
        if (bb) {
          const cExt = transformExtent(bb, 'EPSG:4326', 'EPSG:3857');
          if (isFinite(cExt[0])) m.getView().fit(cExt, opts);
        }
      },
    };
    onMapReady?.(handle);

    return () => {
      window.removeEventListener('resize', onResize);
      map.setTarget(undefined);
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // COG URL 변경 시 source 교체 + 가시성. bbox 가 있고 boundary 가 비어있으면
  // COG 범위로 fit (boundary 가 있으면 그쪽 useEffect 가 우선 fit). bbox 는 ref 에도
  // 보관 — '범위 맞춤' 버튼이 boundary 없을 때 fallback 으로 사용.
  useEffect(() => {
    cogBboxRef.current = cogBbox ?? null;
    const l = cogRef.current;
    if (!l) return;
    if (cogTileUrl) {
      // transition:0 — 반투명(opacity 0.9) COG 타일의 줌 시 페이드 깜빡임 제거.
      l.setSource(new XYZ({ url: cogTileUrl, transition: 0 }));
      l.setVisible(visible.cog);
      if (cogBbox && !boundary?.features?.length) {
        const ext = transformExtent(cogBbox, 'EPSG:4326', 'EPSG:3857');
        const m = mapRef.current;
        if (m && isFinite(ext[0])) {
          m.getView().fit(ext, { padding: [40, 40, 40, 40], duration: 400 });
        }
      }
    } else {
      l.setVisible(false);
    }
  }, [cogTileUrl, cogBbox, visible.cog, boundary]);

  // adminOutline (선택 읍면 + buffer) 변경 시 source 교체.
  useEffect(() => {
    const src = adminSrcRef.current;
    if (!src) return;
    src.clear();
    if (adminOutline && adminOutline.features?.length) {
      const feats = new GeoJSON().readFeatures(adminOutline, {
        dataProjection: 'EPSG:4326',
        featureProjection: 'EPSG:3857',
      });
      src.addFeatures(feats);
    }
  }, [adminOutline]);

  // boundary 변경
  useEffect(() => {
    const src = boundarySrcRef.current;
    if (!src) return;
    src.clear();
    if (boundary && boundary.features?.length) {
      const feats = new GeoJSON().readFeatures(boundary, {
        dataProjection: 'EPSG:4326',
        featureProjection: 'EPSG:3857',
      });
      src.addFeatures(feats);
      // 자동 줌
      const ext = src.getExtent();
      const m = mapRef.current;
      if (m && ext && isFinite(ext[0])) {
        m.getView().fit(ext, { padding: [40, 40, 40, 40], duration: 400 });
      }
    }
  }, [boundary]);

  // markup 변경
  useEffect(() => {
    const src = markupSrcRef.current;
    if (!src) return;
    src.clear();
    if (markup && markup.features?.length) {
      const feats = new GeoJSON().readFeatures(markup, {
        dataProjection: 'EPSG:4326',
        featureProjection: 'EPSG:3857',
      });
      src.addFeatures(feats);
    }
  }, [markup]);

  // 가시성 토글
  useEffect(() => {
    baseRef.current?.setVisible(visible.base);
    adminLayerRef.current?.setVisible(visible.admin);
    boundaryLayerRef.current?.setVisible(visible.ri);
    markupLayerRef.current?.setVisible(visible.markup);
    cogRef.current?.setVisible(visible.cog && !!cogTileUrl);
  }, [visible, cogTileUrl]);

  return <div ref={ref} style={{ width: '100%', height: '100%' }} />;
}

// kind 별 스타일 (라인등록=파랑, 라인삭제=빨강, 속성등록=파란점, 삭제표기=빨간X)
function styleMarkup(feature: { getProperties: () => Record<string, unknown> }) {
  const props = feature.getProperties();
  const kind = String(props.kind ?? 'add');
  switch (kind) {
    case 'delete':
      return new Style({
        stroke: new Stroke({ color: '#dc2626', width: 4 }),
      });
    case 'attr':
      return new Style({
        image: new CircleStyle({
          radius: 7,
          fill: new Fill({ color: '#1d4ed8' }),
          stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
      });
    case 'delete_mark':
      return new Style({
        image: new CircleStyle({
          radius: 8,
          fill: new Fill({ color: 'rgba(220,38,38,0.85)' }),
          stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
      });
    case 'add':
    default:
      return new Style({
        stroke: new Stroke({ color: '#1d4ed8', width: 3 }),
      });
  }
}

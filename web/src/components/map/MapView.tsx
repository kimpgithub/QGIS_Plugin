import { useEffect, useRef } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import XYZ from 'ol/source/XYZ';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import GeoJSON from 'ol/format/GeoJSON';
import { Style, Stroke, Circle as CircleStyle, Fill, Text } from 'ol/style';
import { transformExtent } from 'ol/proj';
import type { MapBrowserEvent } from 'ol';
import type Feature from 'ol/Feature';
import type { FeatureLike } from 'ol/Feature';
import type BaseLayer from 'ol/layer/Base';
import Point from 'ol/geom/Point';
import LineString from 'ol/geom/LineString';
import type { Coordinate } from 'ol/coordinate';
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
  // 삭제표기 스냅 대상 — 행정리경계 소스. 그리기 툴이 경계선에 스냅하도록 전달.
  getBoundarySource: () => VectorSource | null;
  // 행정리(gid) 영역을 노란 펄스로 잠깐 깜빡여 강조 — 목록 더블클릭 이동 시 사용.
  flashBoundary: (gid: number) => void;
  // 마크업(선/점)과 겹치는 행정리 폴리곤들을 노란 펄스로 잠깐 깜빡여 강조
  // — 수정요청 카드 클릭 시 "어느 행정리에 영향을 주는 요청인지" 표시.
  flashIntersectingBoundaries: (markupId: number) => void;
};

type Props = {
  cogTileUrl?: string | null;        // COG 베이스 타일 URL 템플릿
  cogBbox?: [number, number, number, number] | null;  // [minLon,minLat,maxLon,maxLat] EPSG:4326
  adminOutline?: AdminOutlineCollection | null;  // 선택 읍면 + 주변 buffer
  boundary?: GjFeatureCollection | null;         // 행정리경계
  markup?: MarkupCollection | null;              // 수정요청 레이어
  visible: LayerVisibility;
  onMapReady?: (handle: MapHandle) => void;
  infoMode?: boolean;                            // 툴 비활성 상태 = 경계 클릭으로 정보(비고) 확인
  // 정보모드에서 행정리경계 클릭 시 properties 전달 (빈 곳 클릭 = null → 카드 닫기)
  onPickBoundary?: (props: Record<string, unknown> | null) => void;
  highlightId?: number | null;                   // 강조 표시할 마크업 id (삭제 대상)
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
  infoMode,
  onPickBoundary,
  highlightId,
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
  const highlightIdRef = useRef<number | null>(null);
  // 진행 중인 플래시 정리 함수 — 새 플래시 시작/언마운트 시 이전 것을 중단.
  const flashCleanupRef = useRef<(() => void) | null>(null);

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
      // 데이터에 기입력된 행정리명·부호(ri_nm/ri_cd)가 있으면 폴리곤 안에 라벨 표시.
      // 속성등록(파랑) 라벨과 구분되도록 갈색 계열. 줌이 멀면(resolution 큼) 숨김.
      style: (feat, resolution) => styleBoundary(feat, resolution),
      // 라벨 겹침 자동 처리 — 겹치는 텍스트는 OL 이 알아서 숨긴다 (선·채움은 영향 없음)
      declutter: true,
    });
    boundaryLayerRef.current = boundaryLayer;

    const markupSrc = new VectorSource();
    markupSrcRef.current = markupSrc;
    const markupLayer = new VectorLayer({
      source: markupSrc,
      // highlightIdRef 를 클로저로 읽어, 선택된 마크업을 강조 스타일로 렌더.
      style: (feat) => styleMarkup(feat, highlightIdRef.current),
      // 속성등록 라벨끼리(또는 행정리 라벨과) 겹치면 자동으로 숨김.
      // markup 레이어가 boundary 보다 위(나중 추가) → 같은 declutter 그룹에서
      // 수정요청 라벨이 행정리 라벨보다 우선 표시된다.
      declutter: true,
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

    // 피처들을 임시 레이어에 복제해 노란 펄스(채움+외곽선) 애니메이션 후 제거.
    // 새 플래시가 시작되면 이전 것은 즉시 정리. (단일/다중 공용)
    const flashFeatures = (feats: Feature[]) => {
      const m = mapRef.current;
      if (!m || !feats.length) return;

      flashCleanupRef.current?.();

      const flashSrc = new VectorSource();
      feats.forEach((f) => flashSrc.addFeature(f.clone()));
      const flashLayer = new VectorLayer({ source: flashSrc, zIndex: 100 });
      m.addLayer(flashLayer);

      const DURATION = 2000; // ms — 펄스 3회
      const start = performance.now();
      let raf = 0;
      const cleanup = () => {
        cancelAnimationFrame(raf);
        m.removeLayer(flashLayer);
        flashCleanupRef.current = null;
      };
      flashCleanupRef.current = cleanup;

      const tick = (now: number) => {
        const t = (now - start) / DURATION;
        if (t >= 1) {
          cleanup();
          return;
        }
        // 0→1→0 펄스 3회 + 전체적으로 서서히 사라짐
        const pulse = Math.abs(Math.sin(t * Math.PI * 3));
        const fade = 1 - t * 0.6;
        flashLayer.setStyle(
          new Style({
            stroke: new Stroke({
              color: `rgba(250,204,21,${(0.5 + 0.5 * pulse) * fade})`,
              width: 3 + 5 * pulse,
            }),
            fill: new Fill({
              color: `rgba(250,204,21,${0.35 * pulse * fade})`,
            }),
          })
        );
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    };

    const handle: MapHandle = {
      getMap: () => mapRef.current,
      getBoundarySource: () => boundarySrcRef.current,
      // 행정리(gid) 1개 플래시 — 행정리 목록 더블클릭 이동 시 사용.
      flashBoundary: (gid: number) => {
        const src = boundarySrcRef.current;
        if (!src) return;
        const feat = src
          .getFeatures()
          .find((f) => Number(f.get('gid')) === gid);
        if (feat) flashFeatures([feat]);
      },
      // 마크업(선/점)과 겹치는 행정리 폴리곤들을 한꺼번에 플래시.
      // 점 = 점이 들어있는 폴리곤, 선 = 선이 지나가는 폴리곤 전부.
      flashIntersectingBoundaries: (markupId: number) => {
        const mSrc = markupSrcRef.current;
        const bSrc = boundarySrcRef.current;
        if (!mSrc || !bSrc) return;
        const markupFeat = mSrc
          .getFeatures()
          .find((f) => Number(f.get('id')) === markupId);
        const geom = markupFeat?.getGeometry();
        if (!geom) return;

        // 1차: bbox 로 후보 추림 → 2차: 선/점을 따라 찍은 좌표의 폴리곤 포함 여부로 판정
        const candidates: Feature[] = [];
        bSrc.forEachFeatureIntersectingExtent(geom.getExtent(), (f) => {
          candidates.push(f as Feature);
        });
        const samples = sampleCoords(geom as Point | LineString);
        const hits = candidates.filter((bf) => {
          const poly = bf.getGeometry();
          if (!poly) return false;
          return samples.some((c) => poly.intersectsCoordinate(c));
        });
        flashFeatures(hits);
      },
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
      flashCleanupRef.current?.();
      map.setTarget(undefined);
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // COG URL 변경 시 source 교체 + 가시성. bbox 가 있고 boundary 가 비어있으면
  // COG 범위로 fit (boundary 가 있으면 그쪽 useEffect 가 우선 fit). bbox 는 ref 에도
  // 보관 — '범위 맞춤' 버튼이 boundary 없을 때 fallback 으로 사용.
  const lastFitCogRef = useRef<string | null>(null);
  useEffect(() => {
    cogBboxRef.current = cogBbox ?? null;
    const l = cogRef.current;
    if (!l) return;
    if (cogTileUrl) {
      // transition:0 — 반투명(opacity 0.9) COG 타일의 줌 시 페이드 깜빡임 제거.
      l.setSource(new XYZ({ url: cogTileUrl, transition: 0 }));
      l.setVisible(visible.cog);
      if (cogBbox) {
        // 레이어 extent 를 이미지 bbox 로 제한 — 범위 밖 타일 요청을 막아
        // titiler 의 TileOutsideBounds 404 가 콘솔에 도배되는 것을 방지.
        const ext = transformExtent(cogBbox, 'EPSG:4326', 'EPSG:3857');
        if (isFinite(ext[0])) l.setExtent(ext);
        const m = mapRef.current;
        // fit 은 COG 가 처음/새로 로드될 때만 (boundary 없는 읍면 한정)
        if (
          m &&
          isFinite(ext[0]) &&
          !boundary?.features?.length &&
          lastFitCogRef.current !== cogTileUrl
        ) {
          lastFitCogRef.current = cogTileUrl;
          m.getView().fit(ext, { padding: [40, 40, 40, 40], duration: 400 });
        }
      } else {
        l.setExtent(undefined);
      }
    } else {
      l.setVisible(false);
      lastFitCogRef.current = null;
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

  // boundary 변경 — 데이터 교체. 자동 줌(fit)은 "다른 읍면으로 바뀌었을 때"만.
  // (30초 자동 동기화가 같은 읍면 데이터를 다시 내려줄 때마다 fit 하면
  //  사용자가 이동해 둔 화면이 전체 범위로 되돌아가 버림)
  const lastFitAdmRef = useRef<string | null>(null);
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
      // 자동 줌 — 읍면(adm_cd)이 달라졌을 때만
      const admCd = String(boundary.features[0]?.properties?.adm_cd ?? '');
      if (admCd !== lastFitAdmRef.current) {
        lastFitAdmRef.current = admCd;
        const ext = src.getExtent();
        const m = mapRef.current;
        if (m && ext && isFinite(ext[0])) {
          m.getView().fit(ext, { padding: [40, 40, 40, 40], duration: 400 });
        }
      }
    } else {
      lastFitAdmRef.current = null;
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

  // 콜백을 ref 로 보관 — infoMode 효과가 콜백 변경마다 재바인딩되지 않게.
  const onPickBoundaryRef = useRef(onPickBoundary);
  useEffect(() => {
    onPickBoundaryRef.current = onPickBoundary;
  }, [onPickBoundary]);

  // 정보모드(툴 비활성): 행정리경계 클릭 → properties(비고 포함)를 호출부에 전달.
  // 빈 곳 클릭은 null 전달(정보 카드 닫기).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !infoMode) return;
    const onClick = (evt: MapBrowserEvent) => {
      let picked: Record<string, unknown> | null = null;
      map.forEachFeatureAtPixel(
        evt.pixel,
        (feat: FeatureLike) => {
          const { geometry: _g, ...props } = feat.getProperties();
          picked = props;
          return true;
        },
        {
          layerFilter: (l: BaseLayer) => l === boundaryLayerRef.current,
          hitTolerance: 4,
        }
      );
      onPickBoundaryRef.current?.(picked);
    };
    map.on('singleclick', onClick);
    return () => {
      map.un('singleclick', onClick);
    };
  }, [infoMode]);

  // 강조 대상 변경 → ref 갱신 후 마크업 레이어 리렌더(changed).
  useEffect(() => {
    highlightIdRef.current = highlightId ?? null;
    markupLayerRef.current?.changed();
  }, [highlightId]);

  return <div ref={ref} style={{ width: '100%', height: '100%' }} />;
}

// 마크업 geometry 를 따라 겹침 판정용 좌표를 뽑는다.
//  - 점: 그 좌표 1개
//  - 선: 꼭짓점 + 각 구간을 ~20m(EPSG:3857 기준) 간격으로 샘플링한 좌표
//    (선이 폴리곤 꼭짓점 사이를 통과해도 놓치지 않도록)
function sampleCoords(geom: Point | LineString): Coordinate[] {
  if (geom instanceof Point) return [geom.getCoordinates()];
  if (!(geom instanceof LineString)) return [];
  const out: Coordinate[] = [];
  const cs = geom.getCoordinates();
  for (let i = 0; i < cs.length - 1; i++) {
    const [x1, y1] = cs[i];
    const [x2, y2] = cs[i + 1];
    const len = Math.hypot(x2 - x1, y2 - y1);
    // 구간당 최대 200개로 제한 (아주 긴 선에서 과도한 연산 방지)
    const steps = Math.min(Math.max(1, Math.ceil(len / 20)), 200);
    for (let s = 0; s <= steps; s++) {
      out.push([x1 + ((x2 - x1) * s) / steps, y1 + ((y2 - y1) * s) / steps]);
    }
  }
  return out;
}

// 행정리경계 스타일 — 주황 외곽선 + (있으면) 기입력된 행정리명·부호 라벨.
// 라벨은 속성등록(파랑) 요청 라벨과 구분되도록 갈색(#92400e).
// 줌이 멀면(해상도 > 20m/px ≈ 줌 13 미만) 라벨을 숨겨 글자 뒤엉킴 방지 —
// 가까이 들어가면 declutter 가 남은 겹침을 자동 정리한다.
const BOUNDARY_LABEL_MAX_RESOLUTION = 20;

function styleBoundary(
  feature: FeatureLike,
  resolution: number
): Style {
  const riNm = String(feature.get('ri_nm') ?? '').trim();
  const riCd = String(feature.get('ri_cd') ?? '').trim();
  const label = [riNm, riCd].filter(Boolean).join(' · ');
  const showLabel = !!label && resolution < BOUNDARY_LABEL_MAX_RESOLUTION;
  return new Style({
    stroke: new Stroke({ color: '#f59e0b', width: 2 }),
    fill: new Fill({ color: 'rgba(245,158,11,0.04)' }),
    text: showLabel
      ? new Text({
          text: label,
          font: '12px sans-serif',
          fill: new Fill({ color: '#92400e' }),
          // 흰 외곽선 — 배경지도/스캔이미지 위에서도 가독
          stroke: new Stroke({ color: '#ffffff', width: 3 }),
          // 폴리곤 안에 들어가는 경우만 표시 (overflow 끔 → 작은 폴리곤에서 숨김)
          overflow: false,
        })
      : undefined,
  });
}

// kind 별 스타일 (라인등록=파랑, 라인삭제=빨강, 속성등록=파란점, 삭제표기=파란X).
// highlightId 와 feature 의 id 가 일치하면 노란 강조 헤일로를 밑에 깔아 선택 표시.
function styleMarkup(
  feature: { getProperties: () => Record<string, unknown>; get: (k: string) => unknown },
  highlightId?: number | null
): Style | Style[] {
  const props = feature.getProperties();
  const kind = String(props.kind ?? 'add');
  const base = baseMarkupStyle(kind, props);
  const id = feature.get('id');
  if (highlightId != null && id != null && Number(id) === highlightId) {
    return [highlightHalo(kind), base];
  }
  return base;
}

function baseMarkupStyle(kind: string, props?: Record<string, unknown>): Style {
  switch (kind) {
    case 'delete':
      return new Style({ stroke: new Stroke({ color: '#dc2626', width: 4 }) });
    case 'attr': {
      // 속성등록 — 파란 점 + 등록한 행정리명·부호를 라벨로 함께 표시
      // (작업자가 지도만 보고도 어떤 이름/부호 요청인지 알 수 있게).
      const attrs = (props?.attrs ?? {}) as { ri_nm?: string; ri_cd?: string };
      const label = [attrs.ri_nm, attrs.ri_cd]
        .map((s) => (s ?? '').trim())
        .filter(Boolean)
        .join(' · ');
      return new Style({
        image: new CircleStyle({
          radius: 7,
          fill: new Fill({ color: '#1d4ed8' }),
          stroke: new Stroke({ color: '#fff', width: 2 }),
        }),
        text: label
          ? new Text({
              text: label,
              font: 'bold 12px sans-serif',
              offsetY: -16,
              fill: new Fill({ color: '#1d4ed8' }),
              // 흰 외곽선 — 배경지도/스캔이미지 위에서도 가독
              stroke: new Stroke({ color: '#ffffff', width: 3 }),
            })
          : undefined,
      });
    }
    case 'delete_mark':
      // 경계선 삭제표기 — 파란 선 + 선 위에 ✕ 를 일정 간격으로(작업자에게 "여기 삭제" 전달).
      // 캔버스 renderer 로 직접 그려 픽셀 간격/크기를 정밀 제어(✕ 는 흰 헤일로로 항상 가독).
      return DELETE_MARK_STYLE;
    case 'add':
    default:
      return new Style({ stroke: new Stroke({ color: '#1d4ed8', width: 3 }) });
  }
}

// 선택 강조 헤일로 — 점 마크업(attr)은 큰 노란 링, 선 마크업은 굵은 반투명 노란 선.
function highlightHalo(kind: string): Style {
  if (kind === 'attr') {
    return new Style({
      image: new CircleStyle({
        radius: 13,
        fill: new Fill({ color: 'rgba(250,204,21,0.35)' }),
        stroke: new Stroke({ color: '#facc15', width: 3 }),
      }),
    });
  }
  return new Style({
    stroke: new Stroke({ color: 'rgba(250,204,21,0.9)', width: 10 }),
  });
}

// 삭제표기 완성 스타일 — 빨간 선 위에 ✕ 를 26px 간격으로. 캔버스 renderer 로 직접 그림.
// ✕ 는 흰 헤일로(굵게) → 빨간(가늘게) 2패스로 그려 선·지도 어디서나 가독.
const DELETE_MARK_STYLE = new Style({
  renderer: (coords, state) => {
    const ctx = state.context;
    const pr = state.pixelRatio;
    const flat = coords as number[][];
    if (!Array.isArray(flat) || flat.length < 2) return;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // 1) 기본 파란 선 (라인등록과 동일한 파랑)
    ctx.strokeStyle = '#1d4ed8';
    ctx.lineWidth = 3 * pr;
    ctx.beginPath();
    flat.forEach((c, i) => (i === 0 ? ctx.moveTo(c[0], c[1]) : ctx.lineTo(c[0], c[1])));
    ctx.stroke();

    // 2) 선을 따라 ✕ 중심점 수집(픽셀 등간격)
    const spacing = 26 * pr;
    const size = 6 * pr;
    const centers: Array<[number, number]> = [];
    for (let i = 0; i < flat.length - 1; i++) {
      const [x1, y1] = flat[i];
      const [x2, y2] = flat[i + 1];
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy);
      if (len === 0) continue;
      const ux = dx / len;
      const uy = dy / len;
      for (let d = spacing; d < len; d += spacing) {
        centers.push([x1 + ux * d, y1 + uy * d]);
      }
    }
    const strokeXs = () => {
      ctx.beginPath();
      for (const [mx, my] of centers) {
        ctx.moveTo(mx - size, my - size);
        ctx.lineTo(mx + size, my + size);
        ctx.moveTo(mx + size, my - size);
        ctx.lineTo(mx - size, my + size);
      }
      ctx.stroke();
    };
    // 흰 헤일로 → 파란 ✕ (라인등록과 동일한 파랑)
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 5 * pr;
    strokeXs();
    ctx.strokeStyle = '#1d4ed8';
    ctx.lineWidth = 2.5 * pr;
    strokeXs();

    ctx.restore();
  },
});

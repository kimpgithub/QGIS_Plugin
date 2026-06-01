// 최소 GeoJSON 타입 (Geometry/Feature/FC) — @types/geojson 미설치 환경 대응
export type GjPosition = number[];
export type GjGeometry =
  | { type: 'Point'; coordinates: GjPosition }
  | { type: 'LineString'; coordinates: GjPosition[] }
  | { type: 'Polygon'; coordinates: GjPosition[][] }
  | { type: 'MultiLineString'; coordinates: GjPosition[][] }
  | { type: 'MultiPolygon'; coordinates: GjPosition[][][] };

export type GjFeature<P = Record<string, unknown>, G = GjGeometry> = {
  type: 'Feature';
  geometry: G;
  properties: P;
};

export type GjFeatureCollection<P = Record<string, unknown>, G = GjGeometry> = {
  type: 'FeatureCollection';
  features: GjFeature<P, G>[];
};

// 행정 단위 (bnd_adm_pg 한 행)
export type AdminUnit = {
  adm_cd: string;       // 8자리
  adm_nm: string;
  sido_cd: string;      // 2
  sido_nm: string;
  sigungu_cd: string;   // 5
  sigungu_nm: string;
};

export type UserRole = 'user' | 'master';

export type AuthUser = {
  id: string;           // 8자리 행정읍면 코드 또는 마스터 ID
  role: UserRole;
  adm_cd?: string;      // role=user 일 때 본인 담당 읍면
  adm_nm?: string;
  token: string;
};

// 수정요청 종류 — 라인등록/라인삭제/속성등록/삭제표기
export type MarkupKind = 'add' | 'delete' | 'attr' | 'delete_mark';

// lifecycle (단순 한 방향, reopen 없음):
//   pending(요청) → applied(QGIS 반영) → closed(웹 확인 종료)
//   pending → rejected(QGIS 반려, 끝) — 보완은 새 요청 등록으로
export type MarkupStatus = 'pending' | 'applied' | 'rejected' | 'closed';

// 서버 → 클라이언트 (api_client.py 신규 스키마 기준).
// created_by = 작업자(행정구역코드 8자), applied_by/rejected_by/closed_by = 처리자 admin_cd.
// version = 낙관적 잠금 — 상태 변경 요청에 함께 보내 동시 수정 충돌(409)을 감지.
export type Markup = {
  id: number;
  adm_cd: string;
  kind: MarkupKind;
  status: MarkupStatus;
  version: number;
  geometry: GjGeometry;
  attrs: Record<string, unknown> & {
    ri_nm?: string;
    ri_cd?: string;
    note?: string;
  };
  created_by: string;
  created_at: string;
  applied_by?: string | null;
  applied_at?: string | null;
  rejected_by?: string | null;
  rejected_at?: string | null;
  reject_reason?: string | null;
  closed_by?: string | null;
  closed_at?: string | null;
};

// GET /api/boundary feature.properties — QGIS 가 제출한 행정리 경계 + 비고(remark)
export type BoundaryProps = {
  gid: number;
  adm_cd: string;
  adm_nm?: string | null;
  ri_cd?: string | null;
  ri_nm?: string | null;
  status?: string | null;
  remark?: string | null;     // QGIS 작업자 비고
  updated_at?: string | null;
  updated_by?: string | null;
};
export type BoundaryCollection = GjFeatureCollection<BoundaryProps>;

// 서버 응답: GeoJSON FeatureCollection.
// properties 에 geometry 가 같이 들어있으면 ol/format/GeoJSON 가
// setProperties 단계에서 OL Geometry slot 을 raw GeoJSON 으로 덮어쓰므로
// 반드시 Omit. geometry 는 Feature 의 top-level slot 에만 둔다.
export type MarkupProps = Omit<Markup, 'geometry'>;
export type MarkupCollection = GjFeatureCollection<MarkupProps>;

// 새 마크업 등록 payload
export type MarkupCreate = {
  adm_cd: string;
  kind: MarkupKind;
  geometry: GjGeometry;
  attrs?: Markup['attrs'];
};

// /api/cog/{adm_cd} 응답
export type CogInfo = {
  adm_cd: string;
  s3_key: string;
  s3_url: string;
  width: number;
  height: number;
  published_at: string;
  bounds_geojson: GjGeometry | null;
  bbox: [number, number, number, number] | null;   // [minLon, minLat, maxLon, maxLat] in EPSG:4326
  tile_url: string;                                  // {z}/{x}/{y} 템플릿 (same-origin /tiles/...)
  tilejson_url: string;
};

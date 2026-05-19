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

export type MarkupStatus = 'pending' | 'applied' | 'rejected';

// 서버 → 클라이언트 (api_client.py 신규 스키마 기준)
export type Markup = {
  id: number;
  adm_cd: string;
  kind: MarkupKind;
  status: MarkupStatus;
  geometry: GjGeometry;
  attrs: Record<string, unknown> & {
    ri_nm?: string;
    ri_cd?: string;
    note?: string;
  };
  created_by: string;
  created_at: string;
  applied_at?: string | null;
  rejected_at?: string | null;
  reject_reason?: string | null;
};

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

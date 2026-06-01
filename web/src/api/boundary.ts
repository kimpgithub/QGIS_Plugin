import { api } from './client';
import type { BoundaryCollection, GjFeatureCollection } from '../types';

// GET /api/boundary?adm_cd=... — 행정리 경계 GeoJSON (properties 에 remark 포함)
export function getBoundary(
  adm_cd: string
): Promise<BoundaryCollection> {
  return api<BoundaryCollection>('/api/boundary', { query: { adm_cd } });
}

// PUT /api/boundary — 경계 일괄 upsert (작업자 페이지가 발주자 마크업 반영 후 호출)
export function submitBoundary(
  geojson: GjFeatureCollection,
  updated_by?: string
): Promise<{ affected: number; message?: string }> {
  return api('/api/boundary', {
    method: 'PUT',
    body: geojson,
    query: { updated_by },
  });
}

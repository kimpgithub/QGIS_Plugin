import { api } from './client';
import type { GjFeatureCollection, GjGeometry } from '../types';

// admin_outline (bnd_adm_pg) 한 읍면 + 주변 buffer 반환.
// GET /api/admin_outline?adm_cd=&buffer_m=
// properties.is_target=true 가 선택된 읍면, false 가 이웃.
export type AdminOutlineProps = {
  adm_cd: string;
  adm_nm: string;
  sgg_cd: string;
  sgg_nm: string;
  sido_cd: string;
  sido_nm: string;
  is_target: boolean;
};

export type AdminOutlineCollection = GjFeatureCollection<
  AdminOutlineProps,
  GjGeometry
>;

export function getAdminOutline(
  adm_cd: string,
  buffer_m = 1000
): Promise<AdminOutlineCollection> {
  return api<AdminOutlineCollection>('/api/admin_outline', {
    query: { adm_cd, buffer_m },
  });
}

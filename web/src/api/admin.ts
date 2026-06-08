import { api } from './client';

// 관리 현황 페이지(00000000 전용) 전용 API.
// 권한은 서버가 require_superadmin 으로 강제 — 타 계정은 403.

// GET /api/admin/markup-stats — 지역별 수정요청 현황
export type MarkupStat = {
  adm_cd: string;
  adm_nm: string;
  sgg_nm: string;
  sido_nm: string;
  total: number;
  pending: number;
  applied: number;
  rejected: number;
  last_request_at: string | null;
};

export function listMarkupStats(): Promise<MarkupStat[]> {
  return api<MarkupStat[]>('/api/admin/markup-stats');
}

// GET /api/admin/upload-history — 데이터 업로드 이력
export type UploadHistory = {
  adm_cd: string;
  adm_nm: string;
  sgg_nm: string;
  sido_nm: string;
  cog_published_at: string | null;     // 항공사진 업로드 시각
  boundary_count: number | null;       // 경계 행 수
  boundary_updated_at: string | null;  // 경계 최종 업로드 시각
  boundary_updated_by: string | null;
};

export function listUploadHistory(): Promise<UploadHistory[]> {
  return api<UploadHistory[]>('/api/admin/upload-history');
}

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

// GET /api/admin/markup-list — 개별 수정요청 전체 목록(전국·모든 상태)
export type MarkupItem = {
  id: number;
  adm_cd: string;
  adm_nm: string | null;
  kind: 'add' | 'delete' | 'attr' | 'delete_mark';
  status: 'pending' | 'applied' | 'rejected';
  note: string | null;
  created_by: string | null;
  created_at: string;
};

export function listMarkupItems(): Promise<MarkupItem[]> {
  return api<MarkupItem[]>('/api/admin/markup-list');
}

// DELETE /api/admin/markup/{id} — 개별 수정요청 삭제(상태 무관, 복구 불가)
export function deleteMarkupItem(id: number): Promise<{ deleted: number }> {
  return api(`/api/admin/markup/${id}`, { method: 'DELETE' });
}

// DELETE /api/admin/markup — 전국 모든 수정요청 일괄 삭제(복구 불가)
export function deleteAllMarkup(): Promise<{ deleted: number }> {
  return api('/api/admin/markup', { method: 'DELETE' });
}

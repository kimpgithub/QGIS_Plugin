import { api, getToken } from './client';

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

// ── 공간정보 내보내기(전국/시도, master 전용) ────────────────────────────
export type MarkupKindCount = {
  add: number;
  delete_mark: number;
  attr: number;
  total: number;
};
export type SidoSummary = MarkupKindCount & {
  sido_cd: string;
  sido_nm: string;
};
export type MarkupExportSummary = {
  nation: MarkupKindCount;
  sido: SidoSummary[];
};

// GET /api/admin/markup-sido-summary — 시도별 건수(체크리스트 구성용)
export function getMarkupExportSummary(
  status = 'pending'
): Promise<MarkupExportSummary> {
  return api<MarkupExportSummary>('/api/admin/markup-sido-summary', {
    query: { status },
  });
}

// GET /api/admin/markup-export — 선택 범위의 GeoJSON 3종을 ZIP 으로 받아 저장.
// scopes: ['all'](전국) 또는 시도코드 배열 ['11','26',...].
export async function downloadMarkupExport(
  scopes: string[],
  status = 'pending'
): Promise<void> {
  const qs = new URLSearchParams({ scopes: scopes.join(','), status });
  const token = getToken();
  const r = await fetch(`/api/admin/markup-export?${qs.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = j.detail;
    } catch {
      /* 본문 없음 */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const cd = r.headers.get('content-disposition') ?? '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
  const filename = m ? decodeURIComponent(m[1]) : '수정요청_공간정보.zip';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

import { api } from './client';
import type {
  MarkupCollection,
  MarkupCreate,
  MarkupStatus,
} from '../types';

// GET /api/markup?adm_cd=&status=
export function listMarkup(
  adm_cd?: string,
  status: MarkupStatus | 'all' = 'pending'
): Promise<MarkupCollection> {
  return api<MarkupCollection>('/api/markup', {
    query: {
      adm_cd,
      status: status === 'all' ? undefined : status,
    },
  });
}

// POST /api/markup — 새 수정요청 등록
export function createMarkup(
  payload: MarkupCreate
): Promise<{ id: number }> {
  return api('/api/markup', { method: 'POST', body: payload });
}

// 상태 변경 3종은 version(낙관적 잠금)을 함께 보낸다 — 화면이 본 버전과 서버가
// 다르면 409. 호출부는 409 시 목록 새로고침 후 재시도 안내.

// PATCH /api/markup/{id}/reject — 반려 (사유 필수, master/plugin)
export function rejectMarkup(
  id: number,
  reason: string,
  version?: number
): Promise<void> {
  return api(`/api/markup/${id}/reject`, {
    method: 'PATCH',
    body: { reason, version },
  });
}

// PATCH /api/markup/{id}/close — applied 결과 확인·수락 → 종료 (master)
export function closeMarkup(id: number, version?: number): Promise<void> {
  return api(`/api/markup/${id}/close`, {
    method: 'PATCH',
    body: { version },
  });
}

// PATCH /api/markup/{id}/reopen — 다시 pending (applied 거부 / rejected 보완)
export function reopenMarkup(
  id: number,
  reason?: string,
  version?: number
): Promise<void> {
  return api(`/api/markup/${id}/reopen`, {
    method: 'PATCH',
    body: { reason: reason || undefined, version },
  });
}

// 반영(applied)은 QGIS(plugin)만 — 경계 제출과 같은 트랜잭션. 웹에는 apply 경로 없음.

// DELETE /api/markup/{id} — 처리 전(pending) 수정요청 회수(삭제)
export function deleteMarkup(id: number): Promise<void> {
  return api(`/api/markup/${id}`, { method: 'DELETE' });
}

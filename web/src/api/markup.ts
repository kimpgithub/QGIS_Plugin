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

// 라이프사이클 — 처리는 전부 웹에서 (QGIS 와 동기화 없음):
//   대기 → 반영(웹 작업자, 끝) / 대기 → 반려(웹 작업자·사유, 끝)
// 작업자는 요청 카드를 보고 QGIS 로 경계를 수정한 뒤, 여기서 [반영] 또는 [반려] 처리.
// version(낙관적 잠금) 불일치 시 409 — 호출부는 목록 새로고침 후 재시도 안내.

// PATCH /api/markup/{id}/apply — 반영 처리 (master)
export function applyMarkup(id: number, version?: number): Promise<void> {
  return api(`/api/markup/${id}/apply`, {
    method: 'PATCH',
    body: { version },
  });
}

// PATCH /api/markup/{id}/reject — 반려 처리, 사유 필수 (master)
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

// DELETE /api/markup/{id} — 처리 전(pending) 수정요청 회수(삭제)
export function deleteMarkup(id: number): Promise<void> {
  return api(`/api/markup/${id}`, { method: 'DELETE' });
}

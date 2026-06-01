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

// 단순 라이프사이클: 대기 → 반영(QGIS) → 확인종료(웹) / 대기 → 반려(QGIS, 끝).
// 웹이 호출하는 상태 변경은 close(확인) 하나뿐 — 반영/반려는 QGIS 작업자만.
// 반려됐거나 결과가 다르면 새 요청을 등록한다.

// PATCH /api/markup/{id}/close — applied 결과 확인·수락 → 종료 (master)
// version(낙관적 잠금) 불일치 시 409 — 호출부는 목록 새로고침 후 재시도 안내.
export function closeMarkup(id: number, version?: number): Promise<void> {
  return api(`/api/markup/${id}/close`, {
    method: 'PATCH',
    body: { version },
  });
}

// DELETE /api/markup/{id} — 처리 전(pending) 수정요청 회수(삭제)
export function deleteMarkup(id: number): Promise<void> {
  return api(`/api/markup/${id}`, { method: 'DELETE' });
}

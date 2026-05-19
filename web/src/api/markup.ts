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

// PATCH /api/markup/{id}/apply — 반영 완료
export function applyMarkup(id: number): Promise<void> {
  return api(`/api/markup/${id}/apply`, { method: 'PATCH' });
}

// PATCH /api/markup/{id}/reject — 반려 (사유 필수)
export function rejectMarkup(id: number, reason: string): Promise<void> {
  return api(`/api/markup/${id}/reject`, {
    method: 'PATCH',
    body: { reason },
  });
}

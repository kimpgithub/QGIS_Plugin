import { api } from './client';
import type { CogInfo } from '../types';

// GET /api/cog/{adm_cd} — 해당 admin 의 COG 메타 + titiler 타일 URL.
// 업로드 전이면 백엔드가 404 → 호출부에서 catch 해서 null 처리.
export function getCog(adm_cd: string): Promise<CogInfo> {
  return api<CogInfo>(`/api/cog/${encodeURIComponent(adm_cd)}`);
}

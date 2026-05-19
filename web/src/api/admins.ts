import { api } from './client';
import type { AdminUnit } from '../types';

// GET /api/admins
export function listAdmins(): Promise<AdminUnit[]> {
  return api<AdminUnit[]>('/api/admins');
}

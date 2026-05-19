import { useMemo, useState } from 'react';
import Modal from '../common/Modal';
import type { AdminUnit } from '../../types';

type Props = {
  open: boolean;
  admins: AdminUnit[];
  onCancel: () => void;
  onSelect: (a: AdminUnit) => void;
};

export default function AdminPickerModal({
  open,
  admins,
  onCancel,
  onSelect,
}: Props) {
  const [q, setQ] = useState('');
  const [sido, setSido] = useState('');
  const [sgg, setSgg] = useState('');

  const sidoOpts = useMemo(
    () =>
      uniqueBy(admins, 'sido_cd').map((a) => ({
        cd: a.sido_cd,
        nm: a.sido_nm,
      })),
    [admins]
  );

  const sggOpts = useMemo(() => {
    const list = sido ? admins.filter((a) => a.sido_cd === sido) : admins;
    return uniqueBy(list, 'sigungu_cd').map((a) => ({
      cd: a.sigungu_cd,
      nm: a.sigungu_nm,
    }));
  }, [admins, sido]);

  const rows = useMemo(() => {
    let list = admins;
    if (sido) list = list.filter((a) => a.sido_cd === sido);
    if (sgg) list = list.filter((a) => a.sigungu_cd === sgg);
    if (q) {
      const needle = q.toLowerCase();
      list = list.filter(
        (a) =>
          a.adm_cd.includes(needle) ||
          a.adm_nm.toLowerCase().includes(needle)
      );
    }
    return list.slice(0, 300);
  }, [admins, sido, sgg, q]);

  return (
    <Modal open={open} title="행정읍면 선택" onClose={onCancel} width={520}>
      <div style={styles.filters}>
        <select
          style={styles.sel}
          value={sido}
          onChange={(e) => {
            setSido(e.target.value);
            setSgg('');
          }}
        >
          <option value="">전체 시·도</option>
          {sidoOpts.map((o) => (
            <option key={o.cd} value={o.cd}>
              {o.nm}
            </option>
          ))}
        </select>
        <select
          style={styles.sel}
          value={sgg}
          onChange={(e) => setSgg(e.target.value)}
        >
          <option value="">전체 시·군·구</option>
          {sggOpts.map((o) => (
            <option key={o.cd} value={o.cd}>
              {o.nm}
            </option>
          ))}
        </select>
        <input
          style={styles.q}
          placeholder="코드 또는 명칭 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      <div style={styles.list}>
        {rows.length === 0 && <div style={styles.empty}>일치하는 항목 없음</div>}
        {rows.map((a) => (
          <button
            key={a.adm_cd}
            type="button"
            style={styles.row}
            onClick={() => onSelect(a)}
          >
            <span style={styles.code}>{a.adm_cd}</span>
            <span style={styles.nm}>
              {a.sido_nm} {a.sigungu_nm} {a.adm_nm}
            </span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

function uniqueBy<T, K extends keyof T>(arr: T[], key: K): T[] {
  const seen = new Set<unknown>();
  const out: T[] = [];
  for (const x of arr) {
    const k = x[key];
    if (!seen.has(k)) {
      seen.add(k);
      out.push(x);
    }
  }
  return out;
}

const styles: Record<string, React.CSSProperties> = {
  filters: { display: 'flex', gap: 6, marginBottom: 8 },
  sel: {
    flex: 1,
    padding: '6px 8px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  q: {
    flex: 2,
    padding: '6px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  list: {
    maxHeight: 360,
    overflowY: 'auto',
    border: '1px solid #e5e7eb',
    borderRadius: 4,
  },
  row: {
    width: '100%',
    display: 'flex',
    gap: 12,
    padding: '6px 10px',
    background: '#fff',
    border: 'none',
    borderBottom: '1px solid #f1f3f7',
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: 13,
  },
  code: {
    fontFamily: 'ui-monospace, Consolas, monospace',
    color: '#1f6feb',
    minWidth: 80,
  },
  nm: { color: '#1f2937' },
  empty: { textAlign: 'center', padding: 16, color: '#9ca3af', fontSize: 12 },
};

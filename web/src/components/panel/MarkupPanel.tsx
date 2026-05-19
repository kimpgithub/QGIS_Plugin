import { useMemo } from 'react';
import type { Markup, MarkupStatus } from '../../types';
import MarkupCard from './MarkupCard';

type Props = {
  items: Markup[];
  filter: Record<MarkupStatus, boolean>;
  onFilterChange: (f: Record<MarkupStatus, boolean>) => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onApply: (id: number) => void;
  onReject: (id: number) => void;
  loading?: boolean;
};

const FILTER_ROWS: { key: MarkupStatus; label: string }[] = [
  { key: 'pending', label: '미처리' },
  { key: 'applied', label: '반영' },
  { key: 'rejected', label: '반려' },
];

export default function MarkupPanel({
  items,
  filter,
  onFilterChange,
  selectedId,
  onSelect,
  onApply,
  onReject,
  loading,
}: Props) {
  const filtered = useMemo(
    () => items.filter((i) => filter[i.status]),
    [items, filter]
  );

  return (
    <div style={styles.box}>
      <div style={styles.head}>
        <div style={styles.title}>수정요청</div>
        <div style={styles.filters}>
          {FILTER_ROWS.map((r) => (
            <label key={r.key} style={styles.flbl}>
              <input
                type="checkbox"
                checked={filter[r.key]}
                onChange={(e) =>
                  onFilterChange({ ...filter, [r.key]: e.target.checked })
                }
              />
              <span>{r.label}</span>
            </label>
          ))}
        </div>
      </div>
      <div style={styles.list}>
        {loading && <div style={styles.empty}>불러오는 중…</div>}
        {!loading && filtered.length === 0 && (
          <div style={styles.empty}>해당 조건의 요청이 없습니다.</div>
        )}
        {filtered.map((m) => (
          <MarkupCard
            key={m.id}
            item={m}
            selected={m.id === selectedId}
            onClick={() => onSelect(m.id)}
            onApply={() => onApply(m.id)}
            onReject={() => onReject(m.id)}
          />
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    width: 320,
    minWidth: 320,
    borderLeft: '1px solid #d0d3da',
    background: '#fafbfc',
    display: 'flex',
    flexDirection: 'column',
  },
  head: {
    padding: '10px 12px',
    borderBottom: '1px solid #d0d3da',
    background: '#fff',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  title: { fontSize: 13, fontWeight: 600, color: '#1f2937' },
  filters: { display: 'flex', gap: 12 },
  flbl: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 12,
    color: '#374151',
    cursor: 'pointer',
  },
  list: {
    flex: 1,
    overflowY: 'auto',
    padding: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  empty: {
    textAlign: 'center',
    color: '#9ca3af',
    fontSize: 12,
    padding: '20px 0',
  },
};

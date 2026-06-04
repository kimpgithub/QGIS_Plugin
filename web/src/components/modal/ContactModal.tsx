import { useState, type FormEvent } from 'react';
import { setContact } from '../../api/auth';
import { ApiError } from '../../api/client';

type Props = {
  open: boolean;
  // 등록 성공 시 새 내선번호를 부모로 전달 (user 갱신용)
  onRegistered: (contact: string) => void;
};

// 첫 로그인 시 1회 — 닫기 불가(필수 등록). 숫자만 입력.
export default function ContactModal({ open, onRegistered }: Props) {
  const [val, setVal] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    const v = val.trim();
    if (!/^\d+$/.test(v)) {
      setErr('업무연락처는 숫자만 입력하세요(공백 없이).');
      return;
    }
    setBusy(true);
    try {
      const saved = await setContact(v);
      onRegistered(saved);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.body && typeof e.body === 'object' && 'detail' in e.body
          ? String((e.body as { detail: unknown }).detail)
          : '등록 실패 — 잠시 후 다시 시도하세요.';
      setErr(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={styles.backdrop}>
      <form style={styles.card} onSubmit={onSubmit}>
        <div style={styles.head}>업무연락처 등록</div>
        <div style={styles.body}>
          <p style={styles.lead}>
            행정리 구축 검수 및 수정요청을 위한 업무연락처 등록입니다.
          </p>
          <ul style={styles.notes}>
            <li>주1) 개인정보보호를 위해 휴대전화번호는 등록이 불가합니다.</li>
            <li>주2) 공백없이 업무연락처 숫자만 등록해 주세요.</li>
          </ul>
          <label style={styles.row}>
            <span style={styles.label}>담당자 연락처(내선번호)</span>
            <input
              style={styles.input}
              type="text"
              inputMode="numeric"
              autoFocus
              placeholder="숫자만 입력 (예: 0421234567)"
              value={val}
              onChange={(e) => setVal(e.target.value.replace(/\D/g, ''))}
              disabled={busy}
            />
          </label>
          {err && <div style={styles.err}>{err}</div>}
          <button type="submit" style={styles.submit} disabled={busy || !val}>
            {busy ? '등록 중…' : '등록'}
          </button>
        </div>
      </form>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.45)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1100,
  },
  card: {
    width: 460,
    background: '#fff',
    borderRadius: 6,
    boxShadow: '0 6px 30px rgba(0,0,0,0.25)',
  },
  head: {
    padding: '12px 16px',
    borderBottom: '1px solid #d0d3da',
    fontSize: 14,
    fontWeight: 600,
    color: '#1f2937',
  },
  body: { padding: 16, display: 'flex', flexDirection: 'column', gap: 12 },
  lead: { margin: 0, fontSize: 13, color: '#374151', lineHeight: 1.6 },
  notes: {
    margin: 0,
    paddingLeft: 18,
    fontSize: 12.5,
    color: '#dc2626',          // 안내 문구 붉은색
    lineHeight: 1.7,
  },
  row: { display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 },
  label: { fontSize: 13, color: '#374151', fontWeight: 500 },
  input: {
    padding: '9px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 14,
  },
  err: { color: '#b91c1c', fontSize: 13 },
  submit: {
    marginTop: 4,
    padding: '10px 0',
    background: '#1f6feb',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    fontSize: 15,
    cursor: 'pointer',
  },
};

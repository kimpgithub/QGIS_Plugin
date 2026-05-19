import { useState, type FormEvent } from 'react';
import { login } from '../api/auth';
import { useAuth } from '../store/AuthContext';

export default function LoginPage() {
  const { setUser } = useAuth();
  const [id, setId] = useState('');
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!id || !pw) {
      setErr('ID 와 비밀번호를 입력하세요.');
      return;
    }
    setBusy(true);
    try {
      const u = await login(id.trim(), pw);
      setUser(u);
    } catch {
      setErr('로그인 실패 — ID/비밀번호를 확인하세요.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={styles.wrap}>
      <form style={styles.card} onSubmit={onSubmit}>
        <h1 style={styles.title}>행정리경계 검수</h1>
        <p style={styles.lead}>
          행정리 공간정보 검수 및 수정요청 페이지 입니다.
          <br />
          일반인은 접속하실 수 없습니다.
        </p>
        <label style={styles.row}>
          <span style={styles.label}>아 이 디</span>
          <input
            style={styles.input}
            type="text"
            inputMode="numeric"
            autoComplete="username"
            placeholder="행정읍면 8자리 또는 마스터 ID"
            value={id}
            onChange={(e) => setId(e.target.value)}
            disabled={busy}
          />
        </label>
        <label style={styles.row}>
          <span style={styles.label}>비밀번호</span>
          <input
            style={styles.input}
            type="password"
            autoComplete="current-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            disabled={busy}
          />
        </label>
        {err && <div style={styles.err}>{err}</div>}
        <button type="submit" style={styles.submit} disabled={busy}>
          {busy ? '로그인 중…' : '로그인'}
        </button>
      </form>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    width: '100%',
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#f5f6fa',
  },
  card: {
    width: 420,
    padding: '36px 32px',
    border: '1px solid #d0d3da',
    borderRadius: 8,
    background: '#fff',
    boxShadow: '0 4px 20px rgba(0,0,0,0.06)',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  title: {
    margin: 0,
    fontSize: 22,
    color: '#1f2937',
    textAlign: 'center',
  },
  lead: {
    margin: '0 0 8px',
    fontSize: 13,
    color: '#6b7280',
    textAlign: 'center',
    lineHeight: 1.6,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  label: {
    width: 70,
    fontSize: 13,
    color: '#374151',
  },
  input: {
    flex: 1,
    padding: '8px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 14,
  },
  err: {
    color: '#b91c1c',
    fontSize: 13,
    textAlign: 'center',
  },
  submit: {
    marginTop: 8,
    padding: '10px 0',
    background: '#1f6feb',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    fontSize: 15,
    cursor: 'pointer',
  },
};

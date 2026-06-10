// 서버는 시각을 UTC(TIMESTAMPTZ)로 응답 → 화면에는 한국시간(KST, UTC+9)으로 표시.
// en-CA 로케일은 "2026-06-05, 15:45" 형태라 콤마 제거 시 "YYYY-MM-DD HH:mm".
const KST_FMT = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

// ISO 시각 문자열 → "YYYY-MM-DD HH:mm" (한국시간). 값이 없으면 empty 반환.
export function formatKST(s?: string | null, empty = ''): string {
  if (!s) return empty;
  const d = new Date(s);
  // 파싱 실패 시 원본 앞부분이라도 보여줌(깨짐 방지).
  if (Number.isNaN(d.getTime())) return s.replace('T', ' ').slice(0, 16);
  // hour12:false 가 자정을 '24:00' 으로 내는 quirk 보정(날짜는 이미 올바름).
  return KST_FMT.format(d).replace(',', '').replace(' 24:', ' 00:');
}

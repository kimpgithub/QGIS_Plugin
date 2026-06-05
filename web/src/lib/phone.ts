// 업무연락처(내선번호) 표시용 하이픈 포맷.
// 저장은 숫자만(예: 0541234567) 이고, 화면에는 자릿수에 맞춰 054-1234-5678 처럼 보여준다.
// 규칙 외(짧은 내선번호 등)는 가공하지 않고 원본 숫자를 그대로 반환.
export function formatPhone(raw?: string | null): string {
  const d = (raw ?? '').replace(/\D/g, '');
  if (!d) return '';

  // 서울 02 지역번호 (2자리)
  if (d.startsWith('02')) {
    if (d.length === 10) return `${d.slice(0, 2)}-${d.slice(2, 6)}-${d.slice(6)}`; // 02-XXXX-XXXX
    if (d.length === 9) return `${d.slice(0, 2)}-${d.slice(2, 5)}-${d.slice(5)}`; //  02-XXX-XXXX
  }
  // 그 외 3자리 지역번호 / 휴대전화
  if (d.length === 11) return `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`; // 0XX-XXXX-XXXX
  if (d.length === 10) return `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6)}`; // 0XX-XXX-XXXX
  if (d.length === 8) return `${d.slice(0, 4)}-${d.slice(4)}`; //                XXXX-XXXX

  // 4자리 내선번호 등 규칙 밖은 원본 그대로
  return d;
}

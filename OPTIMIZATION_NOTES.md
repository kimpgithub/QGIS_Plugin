# 연산 최적화 점검 노트 (Phase 0)

## 측정 환경
- 데이터: `0417_ test/` (admin_code `21510330` 기장군 철마면, 메인 PDF + 분할 4매 + 스캔 4매)
- 환경: linux, conda env `ocr`, 단일 프로세스
- 스캔 해상도: 9934×14042px @ 300DPI
- SHP: 전국 `bnd_adm_pg.shp` (3559행)

## Stage별 wall-time

| Stage | wall | user | par. ratio | per-admin (1 admin × 4 시트) |
|---|---|---|---|---|
| 1 | 31s | 31s | 1.0 | 31s |
| 2 | 131s | 140s | 1.1 | 131s (33s/scan) |
| 3 | 197s | **34m40s** | **10.5** | 197s (49s/sheet) |
| 4 | 14s | 13s | 0.9 | 14s |
| 5 | 27s | 35s | 1.3 | 27s |
| **Total** | **400s** | | | **~6.7 min / admin** |

**Stage 3은 이미 NumPy/SciPy 내부에서 멀티코어 활용 중** (par. ratio 10.5). CPU bound.

## 만장 스케일 추정 (단일 프로세스)

| Stage | 단위 | 단가 | 만장(10000 admin) | 4프로세스 |
|---|---|---|---|---|
| 1 | admin | 31s | 86h | 22h |
| 2 | scan(시트) | 33s | 367h | 92h |
| 3 | scan(시트) | 49s | 544h | 136h |
| 4 | admin | 14s | 39h | 10h |
| 5 | admin | 27s | 75h | 19h |
| **합** | | | **~46일** | **~12일** |

**병목 순위: 3 (49%) > 2 (33%) > 1 (8%) > 5 (7%) > 4 (3%)**

---

## Stage별 substep 분석

### Stage 1 — PDF georef (31s)
출력 로그(시간 미기재) + 코드 추정:
- **PDF→JPG 변환** (300 DPI, 9934×14042) — 약 5s
- **주황 마스크 + 스켈레톤 + Distance Transform** — 약 2s
- **1차 Powell (기하 기반)** — `cost=386.6px > 10` → 폴백 발동, 약 5s 낭비
- **FFT 폴백 (96 AOI)** — 약 12s, 결과 cost=0.4px ✓
- **GCP 생성 + JGW + VRT** — 약 5s

#### 개선 후보
| # | 항목 | 예상 절감 | 난이도 |
|---|---|---|---|
| 1.1 | **1차 Powell 생략, FFT 우선** — 거의 항상 FFT가 채택됨. cost 임계 조정 또는 순서 뒤집기 | 5s/admin (16%) | 하 |
| 1.2 | PDF→JPG 캐시 — 동일 PDF 재실행 시 변환 생략 | 5s/admin (재실행시) | 하 |
| 1.3 | FFT 96 AOI 병렬화 (멀티프로세스) | 6s/admin | 중 |

### Stage 2 — Scan identify (33s/scan)
- **OCR variant 시도** (admin_code 8자리 헤더 인식) — 여러 전처리 조합
- **분할 PDF SIFT 매칭** — sheet_id 결정. 2/4 시트는 SIFT 실패 → 그리드 폴백
- **시트 캐시** 활용 (sheets_geo 생성) — 1회 비용

#### 개선 후보
| # | 항목 | 예상 절감 | 난이도 |
|---|---|---|---|
| 2.1 | OCR variant 조기 종료 — 첫 성공 시 나머지 스킵 (이미 일부 구현되어 있을 수 있음, 검증 필요) | 10~20s/scan | 하 |
| 2.2 | SIFT 매칭 결과 캐시 — 동일 sheet에 대한 매칭 비용 중복 | 5s/scan | 하 |
| 2.3 | OCR 폴백 그리드 휴리스틱 단순화 — SIFT 실패 케이스(2/4)에서 시간 낭비 검토 | 측정 후 결정 | 중 |
| 2.4 | 멀티프로세스 — scan 단위 trivially parallel | 50% (4코어) | 하 |

### Stage 3 — SIFT + TPS warping (49s/sheet)
로그 측정값:
- **SIFT scan** (50K kp): 9s
- **SIFT sheet** (캐시 미스 시): 7s, **캐시 히트 시 ~0s**
- **FLANN 매칭**: 1~2s
- **MAGSAC 호모그래피**: <1s
- **TPS 워핑** (RBFInterpolator x 2회 + cv2.remap): **22s ← 최대 비용**

#### 개선 후보
| # | 항목 | 예상 절감 | 난이도 |
|---|---|---|---|
| 3.1 | **scan SIFT 디스크 캐시** — 현재 sheet SIFT만 캐시됨. 스캔도 (재실행/디버깅 시) 캐시하면 9s/시트 절감 | 9s/sheet (재실행시) | 하 |
| 3.2 | **TPS RBF x/y 통합 호출** — `RBFInterpolator(cw, cs)` 한 번 호출 (cs는 N×2). 현재 두 번 호출 | 5~10s/sheet | 하 |
| 3.3 | TPS 평가 격자 16→32px — 4배 적은 평가, 정확도 약간 손실 | 10s/sheet | 하 |
| 3.4 | SIFT nfeatures 50K → 30K — 매칭 inlier 12K~14K, 50K는 과다 가능 | 3~4s/sheet | 하 |
| 3.5 | FLANN trees 5→10 + checks 50→32 — 트레이드오프 측정 필요 | 1~2s | 중 |
| 3.6 | sheet SIFT 캐시 영속화 확인 (이미 디스크 pickle 사용 중, 검증) | 7s/sheet | 하 (검증) |
| 3.7 | 멀티프로세스 — sheet 단위 trivially parallel | 75% (4코어) | 하 |

### Stage 4 — Merge (14s/admin)
출력 빠름. 최적화 우선순위 낮음.

### Stage 5 — Boundary validation (27s/admin)
- 23개 인접 읍면 SHP 클리핑 + 경계선 비교
- 317 문제 영역 검출
- PNG 시각화 저장

#### 개선 후보
| # | 항목 | 예상 절감 | 난이도 |
|---|---|---|---|
| 5.1 | SHP 인접 읍면 23개 → AOI 정확히 자르기. 현재 buffer 과다 가능 | 5~10s | 중 |
| 5.2 | 시각화 PNG 해상도 축소 옵션 | 2~3s | 하 |

---

## 우선순위 권고

만장 스케일 가정. 4프로세스 병렬화는 **별개 트랙**(런처/배치 스크립트)으로 처리하고, 단건 최적화부터 검토.

### 즉시 적용 가능 (난이도 하, 추정 30% 단축)
- **3.2** TPS RBF 통합 호출 — 가장 큰 단건 절감 (5~10s/시트)
- **3.4** SIFT nfeatures 50K→30K — 3~4s/시트, 매칭 quality 영향 미미 예상
- **3.3** TPS 격자 16→32px — 정확도 영향 측정 후 결정
- **1.1** Powell 1차 생략 (FFT 우선) — 5s/admin
- **2.1** OCR variant 조기 종료 — 코드 검토 필요

### 측정 필요 (B/C 비교)
- 3.3 격자 변경 시 boundary 정확도 영향 (Stage 5 결과로 확인)
- 3.4 SIFT 30K로 inlier 비율 변화

### 별도 트랙 (인프라)
- 4코어 멀티프로세스 런처 — 만장 처리 시 4배 단축 (46일 → 12일)
- GNU parallel 또는 ProcessPoolExecutor 기반 batch wrapper

---

## 결정 보류

화면정의서 5탭 자동화 파이프라인은 **단건 처리** UX (배치 처리 X). 따라서:
- **단건 wall time 단축**이 우선 (사용자 대기 시간)
- 만장 스케일은 별도 batch CLI로 분리 운용

Phase 1 작업 시 위 "즉시 적용" 항목은 **함께 진행** 가능 (작은 변경). 별도 Phase로 빼지 않고 Phase 1과 병합 권고.

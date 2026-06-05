import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../store/AuthContext';
import MapView, { type LayerVisibility, type MapHandle } from '../components/map/MapView';
import ToolBar, { type ToolId } from '../components/map/ToolBar';
import LayerControls from '../components/map/LayerControls';
import MarkupPanel from '../components/panel/MarkupPanel';
import BoundaryListPanel from '../components/panel/BoundaryListPanel';
import SaveMarkupModal from '../components/modal/SaveMarkupModal';
import RejectReasonModal from '../components/modal/RejectReasonModal';
import AdminPickerModal from '../components/modal/AdminPickerModal';
import ContactModal from '../components/modal/ContactModal';
import DrawHint from '../components/map/DrawHint';
import {
  listMarkup,
  createMarkup,
  applyMarkup,
  rejectMarkup,
  deleteMarkup,
} from '../api/markup';
import Modal from '../components/common/Modal';
import { ApiError } from '../api/client';
import { getBoundary } from '../api/boundary';
import { listAdmins } from '../api/admins';
import { getCog } from '../api/cog';
import {
  getAdminOutline,
  type AdminOutlineCollection,
} from '../api/admin_outline';
import { attachTool, type ActiveTool } from '../components/map/tools';
import type {
  AdminUnit,
  BoundaryCollection,
  BoundaryProps,
  CogInfo,
  GjFeature,
  GjGeometry,
  Markup,
  MarkupCollection,
  MarkupKind,
  MarkupStatus,
} from '../types';

export default function InspectPage() {
  const { user, setUser, signOut } = useAuth();
  const isMaster = user?.role === 'master';
  // 담당자(user) 가 내선번호 미등록 상태면 첫 로그인 등록 모달(필수)을 띄운다.
  const needContact = user?.role === 'user' && !user.contact;
  // 상단 바 내선번호 클릭 시 여는 수정 모달
  const [editContactOpen, setEditContactOpen] = useState(false);

  // 현재 선택된 행정읍면 — user 는 본인 코드, master 는 picker 결과
  const [admin, setAdmin] = useState<AdminUnit | null>(null);
  useEffect(() => {
    if (!isMaster && user?.adm_cd) {
      setAdmin({
        adm_cd: user.adm_cd,
        adm_nm: user.adm_nm ?? '',
        sido_cd: user.adm_cd.slice(0, 2),
        sido_nm: '',
        sigungu_cd: user.adm_cd.slice(0, 5),
        sigungu_nm: '',
      });
    }
  }, [user, isMaster]);

  // 마스터용 admin 목록 (picker 모달). 마스터는 로그인 직후 백지 상태이므로
  // 행정읍면 미선택이면 picker 를 자동으로 띄운다.
  const [admins, setAdmins] = useState<AdminUnit[]>([]);
  const [adminPickerOpen, setAdminPickerOpen] = useState(false);
  useEffect(() => {
    if (isMaster && !admin) setAdminPickerOpen(true);
  }, [isMaster, admin]);
  useEffect(() => {
    if (!isMaster) return;
    listAdmins()
      .then(setAdmins)
      .catch((e) => console.warn('[admins] 로드 실패 (mock 가능)', e));
  }, [isMaster]);

  // 레이어 가시성
  const [visible, setVisible] = useState<LayerVisibility>({
    base: false,   // 배경지도 기본 off — 백지에서 시작, 좌측에서 토글
    cog: true,
    admin: true,
    ri: true,
    markup: true,
  });

  // 데이터
  const [boundary, setBoundary] = useState<BoundaryCollection | null>(null);
  const [items, setItems] = useState<Markup[]>([]);
  const [cog, setCog] = useState<CogInfo | null>(null);
  const [adminOutline, setAdminOutline] = useState<AdminOutlineCollection | null>(null);
  const [loading, setLoading] = useState(false);
  // 클릭한 행정리경계 정보(QGIS 비고 포함) — 툴 비활성 상태에서 경계 클릭 시 표시
  const [boundaryInfo, setBoundaryInfo] = useState<BoundaryProps | null>(null);
  // 정보 카드 위치 — 머리 부분을 드래그해서 옮길 수 있다. null = 기본 위치(좌측 하단).
  const [infoCardPos, setInfoCardPos] = useState<{ x: number; y: number } | null>(null);
  const infoCardRef = useRef<HTMLDivElement | null>(null);
  // 카드 머리 mousedown → window mousemove/mouseup 으로 드래그 추적.
  function onInfoCardDragStart(e: React.MouseEvent) {
    const card = infoCardRef.current;
    if (!card) return;
    e.preventDefault();
    // 현재 화면상 위치(부모 기준)를 시작점으로 — 기본 위치(bottom 고정)에서도 자연스럽게 전환
    const parent = card.offsetParent as HTMLElement | null;
    const parentRect = parent?.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    const origX = rect.left - (parentRect?.left ?? 0);
    const origY = rect.top - (parentRect?.top ?? 0);
    const startX = e.clientX;
    const startY = e.clientY;
    const onMove = (ev: MouseEvent) => {
      setInfoCardPos({
        x: origX + (ev.clientX - startX),
        y: origY + (ev.clientY - startY),
      });
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }
  // 행정리 목록 패널 (행정리명/부호/비고 테이블, 더블클릭 → 위치 이동)
  const [riListOpen, setRiListOpen] = useState(false);

  // 필터 + 선택
  const [filter, setFilter] = useState<Record<MarkupStatus, boolean>>({
    pending: true,
    applied: true,
    rejected: true,
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // admin 선택 시 boundary + markup + cog + admin_outline 로드
  // (cog/admin_outline 은 미존재 시 404/400 → null)
  useEffect(() => {
    if (!admin) return;
    setLoading(true);
    Promise.all([
      getBoundary(admin.adm_cd).catch(() => null),
      listMarkup(admin.adm_cd, 'all').catch(() => null),
      getCog(admin.adm_cd).catch(() => null),
      getAdminOutline(admin.adm_cd).catch(() => null),
    ])
      .then(([b, mk, c, ao]) => {
        setBoundary(b);
        setItems(
          mk
            ? mk.features.map((f) => ({ ...f.properties, geometry: f.geometry }))
            : []
        );
        setCog(c);
        setAdminOutline(ao);
      })
      .finally(() => setLoading(false));
  }, [admin]);

  // markup 을 FC 형태로 MapView 에 전달.
  // properties 에 geometry 가 들어있으면 ol/format/GeoJSON.readFeatures 가
  // setProperties 단계에서 OL Geometry slot 을 raw GeoJSON 으로 덮어써
  // 다음 addFeaturesInternal 에서 getExtent is not a function 으로 터짐.
  const markupFC = useMemo<MarkupCollection | null>(() => {
    if (!items.length) return null;
    return {
      type: 'FeatureCollection',
      features: items.map(({ geometry, ...rest }) => ({
        type: 'Feature',
        geometry,
        properties: rest,
      })),
    };
  }, [items]);

  // 지도 핸들 (fit 등)
  const mapHandleRef = useRef<MapHandle | null>(null);

  // 행정리 목록(도킹 패널) 토글로 지도 폭이 바뀌므로 OL 캔버스 크기 재계산
  useEffect(() => {
    mapHandleRef.current?.getMap()?.updateSize();
  }, [riListOpen]);

  // 활성 툴
  const [tool, setTool] = useState<ToolId>(null);
  const activeToolRef = useRef<ActiveTool | null>(null);
  const [pendingGeom, setPendingGeom] = useState<GjGeometry | null>(null);
  // 그리기 진행 중 여부 (drawstart~drawend). 안내바 버튼 상태 + 단축키 분기에 사용.
  const [drawing, setDrawing] = useState(false);

  const handleToolComplete = useCallback((geom: GjGeometry) => {
    setPendingGeom(geom);
  }, []);

  useEffect(() => {
    const map = mapHandleRef.current?.getMap();
    if (!map) return;
    // 이전 툴 해제
    activeToolRef.current?.detach();
    activeToolRef.current = null;
    setDrawing(false);
    if (!tool) return;
    // 삭제표기(delete_mark)는 행정리경계에 스냅해 경계선을 따라 그림.
    const snapSrc =
      tool === 'delete_mark'
        ? mapHandleRef.current?.getBoundarySource() ?? null
        : null;
    activeToolRef.current = attachTool(
      map,
      tool,
      handleToolComplete,
      setDrawing,
      snapSrc
    );
    return () => {
      activeToolRef.current?.detach();
      activeToolRef.current = null;
      setDrawing(false);
    };
  }, [tool, handleToolComplete]);

  // [요청삭제] 대상 마크업 id — 수정요청 카드 버튼 클릭 → 확인 모달을 거쳐 삭제
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);
  const deleteTarget = useMemo(
    () => items.find((x) => x.id === deleteTargetId) ?? null,
    [items, deleteTargetId]
  );

  async function onConfirmDelete() {
    if (deleteTargetId == null) return;
    try {
      await deleteMarkup(deleteTargetId);
      await reloadMarkup();
    } catch (e) {
      console.error('deleteMarkup 실패', e);
      const status = e instanceof ApiError ? e.status : 0;
      const msg =
        status === 409
          ? '이미 반영된 요청은 삭제할 수 없습니다'
          : status === 403
            ? '권한이 없습니다 — 본인 담당 읍면의 요청만 삭제할 수 있습니다'
            : status === 404
              ? '이미 삭제된 요청입니다'
              : '삭제 실패 — 잠시 후 다시 시도하세요';
      alert(msg);
    } finally {
      setDeleteTargetId(null);
    }
  }

  // 그리기 단축키: Backspace=마지막 점 취소, Esc=그리던 도형 취소(없으면 툴 종료).
  // 입력란 포커스 중에는 무시 (저장 모달 textarea 등).
  useEffect(() => {
    if (!tool) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
      // 저장/삭제확인 모달이 떠 있으면 그리기 단축키 비활성
      if (pendingGeom != null || deleteTargetId != null) return;
      const at = activeToolRef.current;
      if (!at) return;
      if (e.key === 'Backspace') {
        if (drawing) {
          e.preventDefault();
          at.removeLastPoint();
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        if (drawing) at.abort();
        else setTool(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tool, drawing, pendingGeom, deleteTargetId]);

  // 저장 모달 콜백 — attr(속성등록)는 행정리명/부호도 같은 모달에서 함께 받는다.
  async function onSavePending(
    note: string,
    ext: string,
    attrData?: { ri_nm: string; ri_cd: string }
  ) {
    if (!pendingGeom || !tool || !admin) return;
    try {
      await createMarkup({
        adm_cd: admin.adm_cd,
        kind: tool as MarkupKind,
        geometry: pendingGeom,
        attrs: {
          ...(attrData ?? {}),
          ...(note ? { note } : {}),
          ...(ext ? { ext } : {}),
        },
      });
      await reloadMarkup();
    } catch (e) {
      console.error('createMarkup 실패', e);
      alert('저장 실패 — 콘솔 확인');
    } finally {
      setPendingGeom(null);
      activeToolRef.current?.source.clear();
    }
  }

  function onCancelPending() {
    setPendingGeom(null);
    activeToolRef.current?.source.clear();
  }

  async function reloadMarkup() {
    if (!admin) return;
    const mk = await listMarkup(admin.adm_cd, 'all').catch(() => null);
    setItems(
      mk
        ? mk.features.map((f) => ({ ...f.properties, geometry: f.geometry }))
        : []
    );
  }

  async function reloadBoundary() {
    if (!admin) return;
    const b = await getBoundary(admin.adm_cd).catch(() => null);
    if (b) setBoundary(b);
  }

  // 반려 — 사유 입력 모달 대상 id
  const [rejectId, setRejectId] = useState<number | null>(null);

  // 자동 동기화 — 3분 주기로 markup/boundary 재조회.
  // (QGIS 경계 제출, 다른 사용자의 요청/처리 결과를 수동 새로고침 없이 반영)
  // 그리기·모달 진행 중에는 건너뛰어 작업 흐름을 방해하지 않는다.
  const idle =
    tool == null &&
    pendingGeom == null &&
    rejectId == null &&
    deleteTargetId == null;
  useEffect(() => {
    if (!admin || !idle) return;
    const t = window.setInterval(() => {
      reloadMarkup();
      reloadBoundary();
    }, 180_000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin, idle]);

  // 상태 변경 공통 — 409(상태/version 충돌)면 목록을 새로고침해 화면을 서버와 맞춘다.
  async function transition(action: () => Promise<void>, label: string) {
    try {
      await action();
    } catch (e) {
      console.error(e);
      const conflict = e instanceof ApiError && e.status === 409;
      alert(
        conflict
          ? `${label} 실패 — 다른 곳에서 먼저 처리됐습니다. 목록을 새로고침합니다.`
          : `${label} 실패`
      );
    } finally {
      await reloadMarkup();
    }
  }

  const versionOf = (id: number) => items.find((x) => x.id === id)?.version;

  // 처리는 전부 웹에서 — 작업자(master)가 QGIS 로 경계를 고친 뒤 [반영],
  // 수행 불가/오요청은 [반려](사유). 둘 다 종결 상태.
  const onApply = (id: number) =>
    transition(() => applyMarkup(id, versionOf(id)), '반영 처리');

  async function onConfirmReject(reason: string) {
    if (rejectId == null) return;
    const id = rejectId;
    setRejectId(null);
    await transition(() => rejectMarkup(id, reason, versionOf(id)), '반려 처리');
  }

  function onSelectCard(id: number) {
    setSelectedId(id);
    const m = mapHandleRef.current?.getMap();
    if (!m) return;
    const it = items.find((x) => x.id === id);
    if (!it) return;
    const ext = extentOf(it.geometry);
    if (!ext) return;
    m.getView().fit(
      [
        ...projectExtentToWebMerc(ext),
      ],
      {
        padding: [80, 80, 80, 80],
        duration: 400,
        maxZoom: 17,
        // 이동이 끝나면: 선/점 강조(highlightId)는 유지된 채,
        // 겹치는 행정리 폴리곤들을 노란 펄스로 잠시 깜빡여 영향 범위를 보여준다.
        callback: () =>
          mapHandleRef.current?.flashIntersectingBoundaries(id),
      }
    );
  }

  // 공간정보 다운로드 — 라인등록/삭제표기/속성등록 수정요청을 종류별 GeoJSON 파일로
  // 저장(QGIS 에 드래그하면 바로 열림). 현재 패널 필터(상태)에 보이는 것만 대상.
  function onDownloadMarkup() {
    if (!admin) return;
    const targets: Array<{ kind: MarkupKind; label: string }> = [
      { kind: 'add', label: '라인등록' },
      { kind: 'delete_mark', label: '삭제표기' },
      { kind: 'attr', label: '속성등록' },
    ];
    const visibleItems = items.filter((i) => filter[i.status]);
    let fileCount = 0;
    targets.forEach(({ kind, label }, idx) => {
      const feats = visibleItems.filter((i) => i.kind === kind);
      if (!feats.length) return;
      const fc = {
        type: 'FeatureCollection',
        // QGIS 속성 테이블에서 바로 읽히도록 attrs 를 평탄화해서 담는다.
        features: feats.map((i) => ({
          type: 'Feature',
          geometry: i.geometry,
          properties: {
            id: i.id,
            kind: i.kind,
            status: i.status,
            ri_nm: (i.attrs?.ri_nm as string | undefined) ?? null,
            ri_cd: (i.attrs?.ri_cd as string | undefined) ?? null,
            note: (i.attrs?.note as string | undefined) ?? null,
            created_by: i.created_by,
            created_at: i.created_at,
          },
        })),
      };
      // 브라우저가 연속 다운로드를 막지 않도록 파일 간 약간의 시차를 둔다.
      window.setTimeout(() => {
        downloadJson(fc, `수정요청_${label}_${admin.adm_cd}.geojson`);
      }, idx * 300);
      fileCount++;
    });
    if (fileCount === 0) {
      alert('다운로드할 수정요청이 없습니다 (라인등록/삭제표기/속성등록).');
    }
  }

  // 행정리 목록 행 더블클릭 → 해당 행정리 영역으로 화면 이동 후 노란 펄스 플래시
  function onZoomToBoundary(f: GjFeature<BoundaryProps>) {
    const m = mapHandleRef.current?.getMap();
    if (!m) return;
    const ext = extentOf(f.geometry);
    if (!ext) return;
    const gid = f.properties.gid;
    m.getView().fit(projectExtentToWebMerc(ext), {
      padding: [80, 80, 80, 80],
      duration: 400,
      maxZoom: 17,
      // 이동 애니메이션이 끝난 뒤 해당 영역을 깜빡여 위치를 알려준다.
      callback: () => mapHandleRef.current?.flashBoundary(gid),
    });
  }

  const adminLabel = admin
    ? `${admin.adm_cd} · ${admin.adm_nm || ''}`
    : '행정읍면 미선택';

  return (
    <div style={styles.wrap}>
      <ToolBar
        active={tool}
        onChange={setTool}
        showAdminPicker={isMaster}
        onOpenAdminPicker={() => setAdminPickerOpen(true)}
        adminLabel={adminLabel}
        userId={user?.id}
        contact={user?.contact}
        onLogout={signOut}
      />
      <div style={styles.body}>
        <LayerControls
          visible={visible}
          onToggle={(k, v) => setVisible((s) => ({ ...s, [k]: v }))}
          onFitBoundary={() => mapHandleRef.current?.fitToBoundary()}
          onToggleRiList={() => setRiListOpen((v) => !v)}
          riListOpen={riListOpen}
        />
        {/* 행정리 목록 — 지도 옆 도킹 패널 (지도를 가리지 않음).
            ri_nm/ri_cd/remark 테이블, 행 더블클릭 → 위치 이동 + 플래시 */}
        <BoundaryListPanel
          open={riListOpen}
          boundary={boundary}
          onClose={() => setRiListOpen(false)}
          onZoomTo={onZoomToBoundary}
        />
        <div style={styles.mapWrap}>
          <MapView
            visible={visible}
            cogTileUrl={cog?.tile_url ?? null}
            cogBbox={cog?.bbox ?? null}
            adminOutline={adminOutline}
            boundary={boundary}
            markup={markupFC}
            onMapReady={(h) => (mapHandleRef.current = h)}
            infoMode={tool == null}
            onPickBoundary={(p) => setBoundaryInfo(p as BoundaryProps | null)}
            // 삭제 대상 또는 패널에서 선택한 카드의 마크업을 노란 강조 표시
            highlightId={deleteTargetId ?? selectedId}
          />
          {tool && pendingGeom == null && (
            <DrawHint
              kind={tool}
              drawing={drawing}
              isLine={tool === 'add' || tool === 'delete_mark'}
              onUndoPoint={() => activeToolRef.current?.removeLastPoint()}
              onAbort={() => activeToolRef.current?.abort()}
              onExit={() => setTool(null)}
            />
          )}
          {/* 경계 클릭 정보 카드 — 행정리 속성 + QGIS 작업자 비고(remark).
              머리 부분을 드래그하면 원하는 위치로 옮길 수 있다. */}
          {tool == null && boundaryInfo && (
            <div
              ref={infoCardRef}
              style={{
                ...styles.infoCard,
                ...(infoCardPos
                  ? { left: infoCardPos.x, top: infoCardPos.y, bottom: 'auto' }
                  : {}),
              }}
            >
              <div
                style={styles.infoHead}
                onMouseDown={onInfoCardDragStart}
                title="드래그해서 이동"
              >
                <b>
                  {boundaryInfo.ri_nm || '(행정리명 없음)'}
                  {boundaryInfo.ri_cd ? ` · ${boundaryInfo.ri_cd}` : ''}
                </b>
                <button
                  type="button"
                  style={styles.infoClose}
                  onClick={() => setBoundaryInfo(null)}
                  onMouseDown={(e) => e.stopPropagation()}
                >
                  ✕
                </button>
              </div>
              <div style={styles.infoRow}>
                {boundaryInfo.adm_nm || ''} ({boundaryInfo.adm_cd})
              </div>
              {boundaryInfo.remark && (
                <div style={styles.infoRemark}>
                  <span style={styles.infoRemarkLabel}>작업자 비고</span>
                  {boundaryInfo.remark}
                </div>
              )}
              <div style={styles.infoMeta}>
                수정 {boundaryInfo.updated_by || '-'} ·{' '}
                {(boundaryInfo.updated_at || '').replace('T', ' ').slice(0, 16)}
              </div>
            </div>
          )}
        </div>
        <MarkupPanel
          items={items}
          filter={filter}
          onFilterChange={setFilter}
          selectedId={selectedId}
          onSelect={onSelectCard}
          onApply={onApply}
          onReject={(id) => setRejectId(id)}
          // [요청삭제] — 카드를 선택(지도 강조)하면서 확인 모달을 띄운다
          onDelete={(id) => {
            setSelectedId(id);
            setDeleteTargetId(id);
          }}
          onDownload={onDownloadMarkup}
          canProcess={isMaster}
          loading={loading}
        />
      </div>

      <SaveMarkupModal
        open={pendingGeom != null && tool != null}
        kind={(tool ?? 'add') as MarkupKind}
        defaultExt={user?.contact}
        onCancel={onCancelPending}
        onSave={onSavePending}
      />
      <RejectReasonModal
        open={rejectId != null}
        onCancel={() => setRejectId(null)}
        onSave={onConfirmReject}
      />
      {/* 첫 로그인 — 업무연락처(내선번호) 필수 등록. 닫기 불가. */}
      <ContactModal
        open={!!needContact}
        onRegistered={(contact) => user && setUser({ ...user, contact })}
      />
      {/* 상단 바 내선번호 클릭 — 재수정(취소 가능) */}
      <ContactModal
        open={editContactOpen}
        mode="edit"
        initial={user?.contact}
        onClose={() => setEditContactOpen(false)}
        onRegistered={(contact) => {
          if (user) setUser({ ...user, contact });
          setEditContactOpen(false);
        }}
      />
      <AdminPickerModal
        open={adminPickerOpen}
        admins={admins}
        onCancel={() => setAdminPickerOpen(false)}
        onSelect={(a) => {
          setAdmin(a);
          setAdminPickerOpen(false);
        }}
      />

      {/* [요청삭제] 확인 모달 — 바로 지우지 않고 한 번 더 확인받는다 */}
      <Modal
        open={deleteTargetId != null}
        title="수정요청 삭제"
        onClose={() => setDeleteTargetId(null)}
        width={400}
      >
        <div style={styles.delQ}>
          정말 삭제하시겠습니까?
          {deleteTarget
            ? ` 선택한 수정요청 (#${deleteTarget.id} · ${KIND_LABEL[deleteTarget.kind]})이(가) 삭제됩니다.`
            : ''}
        </div>
        <div style={styles.delNote}>
          삭제하면 복구할 수 없습니다. 대기·반려 요청만 삭제됩니다(반영된 요청 제외).
        </div>
        <div style={styles.delActions}>
          <button
            type="button"
            style={styles.delCancel}
            onClick={() => setDeleteTargetId(null)}
          >
            취소
          </button>
          <button type="button" style={styles.delConfirm} onClick={onConfirmDelete}>
            삭제
          </button>
        </div>
      </Modal>
    </div>
  );
}

const KIND_LABEL: Record<MarkupKind, string> = {
  add: '라인등록',
  delete: '라인삭제',
  attr: '속성등록',
  delete_mark: '삭제표기',
};

// GeoJSON 객체 → 파일 다운로드 (브라우저 메모리에서 생성, 서버 요청 없음)
function downloadJson(obj: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], {
    type: 'application/geo+json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Geometry → bbox [minX,minY,maxX,maxY] (lon/lat 좌표 기준)
function extentOf(g: GjGeometry): [number, number, number, number] | null {
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  const visit = (c: number[]) => {
    if (c.length < 2) return;
    if (c[0] < xmin) xmin = c[0];
    if (c[1] < ymin) ymin = c[1];
    if (c[0] > xmax) xmax = c[0];
    if (c[1] > ymax) ymax = c[1];
  };
  switch (g.type) {
    case 'Point': visit(g.coordinates); break;
    case 'LineString': g.coordinates.forEach(visit); break;
    case 'Polygon':
    case 'MultiLineString': g.coordinates.flat().forEach(visit); break;
    case 'MultiPolygon': g.coordinates.flat(2).forEach(visit); break;
  }
  if (!isFinite(xmin)) return null;
  return [xmin, ymin, xmax, ymax];
}

// 4326 → 3857 (간단 변환, ol/proj 의존 회피)
function projectExtentToWebMerc(
  [xmin, ymin, xmax, ymax]: [number, number, number, number]
): [number, number, number, number] {
  const R = 6378137;
  const toX = (lon: number) => (lon * Math.PI * R) / 180;
  const toY = (lat: number) =>
    Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) * R;
  return [toX(xmin), toY(ymin), toX(xmax), toY(ymax)];
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: '#fff',
  },
  body: { flex: 1, display: 'flex', minHeight: 0 },
  mapWrap: { flex: 1, position: 'relative', minWidth: 0 },
  infoCard: {
    position: 'absolute',
    left: 12,
    bottom: 12,
    width: 280,
    background: '#fff',
    border: '1px solid #d0d3da',
    borderRadius: 6,
    boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
    padding: 12,
    fontSize: 12,
    color: '#1f2937',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    zIndex: 10,
  },
  infoHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 13,
    cursor: 'move',          // 머리 부분 드래그로 카드 이동
    userSelect: 'none',
  },
  infoClose: {
    border: 'none',
    background: 'none',
    cursor: 'pointer',
    color: '#9ca3af',
    fontSize: 13,
    padding: 0,
  },
  infoRow: { color: '#6b7280' },
  infoRemark: {
    background: '#fef9c3',
    border: '1px solid #fde047',
    borderRadius: 4,
    padding: '6px 8px',
    lineHeight: 1.5,
    whiteSpace: 'pre-wrap',
  },
  infoRemarkLabel: {
    display: 'block',
    fontSize: 11,
    fontWeight: 600,
    color: '#a16207',
    marginBottom: 2,
  },
  infoMeta: { fontSize: 11, color: '#9ca3af' },
  delQ: { fontSize: 13, color: '#1f2937', marginBottom: 8, lineHeight: 1.5 },
  delNote: { fontSize: 12, color: '#9ca3af', marginBottom: 12 },
  delActions: { display: 'flex', gap: 8, justifyContent: 'flex-end' },
  delCancel: {
    padding: '6px 16px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
  delConfirm: {
    padding: '6px 16px',
    border: '1px solid #dc2626',
    background: '#dc2626',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
};

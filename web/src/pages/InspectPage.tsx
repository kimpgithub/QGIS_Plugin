import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../store/AuthContext';
import MapView, { type LayerVisibility, type MapHandle } from '../components/map/MapView';
import ToolBar, { type ToolId } from '../components/map/ToolBar';
import LayerControls from '../components/map/LayerControls';
import MarkupPanel from '../components/panel/MarkupPanel';
import SaveMarkupModal from '../components/modal/SaveMarkupModal';
import RejectReasonModal from '../components/modal/RejectReasonModal';
import AdminPickerModal from '../components/modal/AdminPickerModal';
import DrawHint from '../components/map/DrawHint';
import {
  listMarkup,
  createMarkup,
  rejectMarkup,
  closeMarkup,
  reopenMarkup,
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
  GjGeometry,
  Markup,
  MarkupCollection,
  MarkupKind,
  MarkupStatus,
} from '../types';

export default function InspectPage() {
  const { user, signOut } = useAuth();
  const isMaster = user?.role === 'master';

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

  // 필터 + 선택
  const [filter, setFilter] = useState<Record<MarkupStatus, boolean>>({
    pending: true,
    applied: true,
    rejected: true,
    closed: true,
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
    // 라인삭제(delete)는 그리기가 아니라 마크업 선택·삭제 모드 — Draw 미부착.
    // MapView 의 eraseMode 가 hit-test 클릭을 처리한다.
    if (!tool || tool === 'delete') return;
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

  // 삭제모드에서 클릭된 마크업 id (삭제 확인 모달 대상)
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

  // Ctrl+드래그 박스로 고른 다중 삭제 대상 id 목록 (일괄 삭제 확인 모달)
  const [deleteManyIds, setDeleteManyIds] = useState<number[] | null>(null);

  async function onConfirmDeleteMany() {
    const ids = deleteManyIds;
    if (!ids || !ids.length) {
      setDeleteManyIds(null);
      return;
    }
    // 일부는 applied(409)/권한(403) 으로 실패할 수 있음 — 개별 처리 후 실패 id 명시.
    const results = await Promise.allSettled(ids.map((id) => deleteMarkup(id)));
    const ok = results.filter((r) => r.status === 'fulfilled').length;
    const failedIds = ids.filter((_, i) => results[i].status === 'rejected');
    await reloadMarkup();
    setDeleteManyIds(null);
    if (failedIds.length > 0) {
      alert(
        `${ok}건 삭제, ${failedIds.length}건 실패 — #${failedIds.join(', #')}\n` +
          '(이미 반영됐거나 권한이 없는 요청)'
      );
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
      if (
        pendingGeom != null ||
        deleteTargetId != null ||
        deleteManyIds != null
      )
        return;
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
  }, [tool, drawing, pendingGeom, deleteTargetId, deleteManyIds]);

  // 저장 모달 콜백 — attr(속성등록)는 행정리명/부호도 같은 모달에서 함께 받는다.
  async function onSavePending(
    note: string,
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

  // 반려
  const [rejectId, setRejectId] = useState<number | null>(null);

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

  // QGIS 작업 결과 자동 동기화 — 30초 주기로 markup/boundary 재조회.
  // 그리기·모달 진행 중에는 건너뛰어 작업 흐름을 방해하지 않는다.
  const idle =
    tool == null &&
    pendingGeom == null &&
    rejectId == null &&
    deleteTargetId == null &&
    deleteManyIds == null;
  useEffect(() => {
    if (!admin || !idle) return;
    const t = window.setInterval(() => {
      reloadMarkup();
      reloadBoundary();
    }, 30_000);
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

  // applied 결과 확인·수락 → 종료(closed)
  const onClose = (id: number) =>
    transition(() => closeMarkup(id, versionOf(id)), '확인 처리');

  // 다시 대기로 — applied 거부(발주자) 또는 rejected 보완(재요청)
  function onReopen(id: number) {
    const reason = window.prompt('되돌리는 사유(선택):') ?? undefined;
    return transition(
      () => reopenMarkup(id, reason || undefined, versionOf(id)),
      '되돌리기'
    );
  }

  async function onConfirmReject(reason: string) {
    if (rejectId == null) return;
    const id = rejectId;
    setRejectId(null);
    await transition(() => rejectMarkup(id, reason, versionOf(id)), '반려');
  }

  function onSelectCard(id: number) {
    setSelectedId(id);
    // Extent×1.5 줌 — MVP 로 단순히 해당 geometry 의 fit 사용
    const m = mapHandleRef.current?.getMap();
    if (!m) return;
    const it = items.find((x) => x.id === id);
    if (!it) return;
    // TODO: kind=attr 인 경우 intersect 폴리곤 extent
    const ext = extentOf(it.geometry);
    if (!ext) return;
    m.getView().fit(
      [
        ...projectExtentToWebMerc(ext),
      ],
      { padding: [80, 80, 80, 80], duration: 400, maxZoom: 17 }
    );
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
        onLogout={signOut}
      />
      <div style={styles.body}>
        <LayerControls
          visible={visible}
          onToggle={(k, v) => setVisible((s) => ({ ...s, [k]: v }))}
          onFitBoundary={() => mapHandleRef.current?.fitToBoundary()}
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
            eraseMode={tool === 'delete'}
            infoMode={tool == null}
            onPickMarkup={(id) => setDeleteTargetId(id)}
            onPickMarkupMany={(ids) => setDeleteManyIds(ids)}
            onPickBoundary={(p) => setBoundaryInfo(p as BoundaryProps | null)}
            highlightId={deleteTargetId}
          />
          {tool && pendingGeom == null && (
            <DrawHint
              kind={tool}
              drawing={drawing}
              isLine={tool === 'add' || tool === 'delete_mark'}
              eraseMode={tool === 'delete'}
              onUndoPoint={() => activeToolRef.current?.removeLastPoint()}
              onAbort={() => activeToolRef.current?.abort()}
              onExit={() => setTool(null)}
            />
          )}
          {/* 경계 클릭 정보 카드 — 행정리 속성 + QGIS 작업자 비고(remark) */}
          {tool == null && boundaryInfo && (
            <div style={styles.infoCard}>
              <div style={styles.infoHead}>
                <b>
                  {boundaryInfo.ri_nm || '(행정리명 없음)'}
                  {boundaryInfo.ri_cd ? ` · ${boundaryInfo.ri_cd}` : ''}
                </b>
                <button
                  type="button"
                  style={styles.infoClose}
                  onClick={() => setBoundaryInfo(null)}
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
          onReject={(id) => setRejectId(id)}
          onClose={onClose}
          onReopen={onReopen}
          canProcess={isMaster}
          loading={loading}
        />
      </div>

      <SaveMarkupModal
        open={pendingGeom != null && tool != null}
        kind={(tool ?? 'add') as MarkupKind}
        onCancel={onCancelPending}
        onSave={onSavePending}
      />
      <RejectReasonModal
        open={rejectId != null}
        onCancel={() => setRejectId(null)}
        onSave={onConfirmReject}
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

      <Modal
        open={deleteTargetId != null}
        title="수정요청 삭제"
        onClose={() => setDeleteTargetId(null)}
        width={400}
      >
        <div style={styles.delQ}>
          선택한 수정요청{deleteTarget ? ` (#${deleteTarget.id} · ${KIND_LABEL[deleteTarget.kind]})` : ''}을(를)
          삭제하시겠습니까?
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

      <Modal
        open={deleteManyIds != null}
        title="수정요청 일괄 삭제"
        onClose={() => setDeleteManyIds(null)}
        width={400}
      >
        <div style={styles.delQ}>
          선택한 {deleteManyIds?.length ?? 0}건의 수정요청을 삭제하시겠습니까?
        </div>
        <div style={styles.delNote}>
          Ctrl+드래그로 선택한 범위입니다. 대기·반려 요청만 삭제되고
          반영된 요청은 건너뜁니다.
        </div>
        <div style={styles.delActions}>
          <button
            type="button"
            style={styles.delCancel}
            onClick={() => setDeleteManyIds(null)}
          >
            취소
          </button>
          <button
            type="button"
            style={styles.delConfirm}
            onClick={onConfirmDeleteMany}
          >
            삭제 ({deleteManyIds?.length ?? 0}건)
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

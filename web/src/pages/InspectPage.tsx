import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../store/AuthContext';
import MapView, { type LayerVisibility, type MapHandle } from '../components/map/MapView';
import ToolBar, { type ToolId } from '../components/map/ToolBar';
import LayerControls from '../components/map/LayerControls';
import MarkupPanel from '../components/panel/MarkupPanel';
import SaveMarkupModal from '../components/modal/SaveMarkupModal';
import RejectReasonModal from '../components/modal/RejectReasonModal';
import AttrFormModal from '../components/modal/AttrFormModal';
import AdminPickerModal from '../components/modal/AdminPickerModal';
import { listMarkup, createMarkup, applyMarkup, rejectMarkup } from '../api/markup';
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
  CogInfo,
  GjFeatureCollection,
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

  // 마스터용 admin 목록 (picker 모달)
  const [admins, setAdmins] = useState<AdminUnit[]>([]);
  const [adminPickerOpen, setAdminPickerOpen] = useState(false);
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
  const [boundary, setBoundary] = useState<GjFeatureCollection | null>(null);
  const [items, setItems] = useState<Markup[]>([]);
  const [cog, setCog] = useState<CogInfo | null>(null);
  const [adminOutline, setAdminOutline] = useState<AdminOutlineCollection | null>(null);
  const [loading, setLoading] = useState(false);

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

  // 활성 툴
  const [tool, setTool] = useState<ToolId>(null);
  const activeToolRef = useRef<ActiveTool | null>(null);
  const [pendingGeom, setPendingGeom] = useState<GjGeometry | null>(null);
  const [attrOpen, setAttrOpen] = useState(false);

  const handleToolComplete = useCallback((geom: GjGeometry) => {
    setPendingGeom(geom);
  }, []);

  useEffect(() => {
    const map = mapHandleRef.current?.getMap();
    if (!map) return;
    // 이전 툴 해제
    activeToolRef.current?.detach();
    activeToolRef.current = null;
    if (!tool) return;
    activeToolRef.current = attachTool(map, tool, handleToolComplete);
    return () => {
      activeToolRef.current?.detach();
      activeToolRef.current = null;
    };
  }, [tool, handleToolComplete]);

  // 저장 모달 콜백
  async function onSavePending(note: string) {
    if (!pendingGeom || !tool || !admin) return;
    try {
      if (tool === 'attr') {
        // attr 는 별도 폼 모달로 전환
        setAttrOpen(true);
        return;
      }
      await createMarkup({
        adm_cd: admin.adm_cd,
        kind: tool as MarkupKind,
        geometry: pendingGeom,
        attrs: note ? { note } : {},
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

  async function onSaveAttr(data: { ri_nm: string; ri_cd: string }) {
    if (!pendingGeom || !admin) return;
    try {
      await createMarkup({
        adm_cd: admin.adm_cd,
        kind: 'attr',
        geometry: pendingGeom,
        attrs: data,
      });
      await reloadMarkup();
    } catch (e) {
      console.error('createMarkup(attr) 실패', e);
      alert('저장 실패 — 콘솔 확인');
    } finally {
      setAttrOpen(false);
      setPendingGeom(null);
      activeToolRef.current?.source.clear();
    }
  }

  function onCancelPending() {
    setPendingGeom(null);
    setAttrOpen(false);
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

  async function onApply(id: number) {
    try {
      await applyMarkup(id);
      await reloadMarkup();
    } catch (e) {
      console.error(e);
      alert('반영 실패');
    }
  }

  async function onConfirmReject(reason: string) {
    if (rejectId == null) return;
    try {
      await rejectMarkup(rejectId, reason);
      await reloadMarkup();
    } catch (e) {
      console.error(e);
      alert('반려 실패');
    } finally {
      setRejectId(null);
    }
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
          />
        </div>
        <MarkupPanel
          items={items}
          filter={filter}
          onFilterChange={setFilter}
          selectedId={selectedId}
          onSelect={onSelectCard}
          onApply={onApply}
          onReject={(id) => setRejectId(id)}
          loading={loading}
        />
      </div>

      <SaveMarkupModal
        open={pendingGeom != null && tool != null && !attrOpen}
        kind={(tool ?? 'add') as MarkupKind}
        onCancel={onCancelPending}
        onSave={onSavePending}
      />
      <AttrFormModal
        open={attrOpen}
        onCancel={onCancelPending}
        onSave={onSaveAttr}
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
    </div>
  );
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
};

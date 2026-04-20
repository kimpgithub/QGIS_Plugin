"""PostGIS 연결 관리 — QgsSettings 기반 프로파일 저장/로드 + 연결 테스트.

화면정의서 요구: PostGIS에 배경지도/경계/연속지적도 탑재 → 행정리 작업 DB(bnd_job_pg) 편집.
프로파일(여러 환경) 저장 지원 — 개발/운영 등 전환 편의.
"""
from dataclasses import dataclass, asdict


SETTINGS_PREFIX = 'gis_scan_tools/db'
DEFAULT_PROFILE = 'default'


@dataclass
class PGProfile:
    """PostGIS 연결 프로파일."""
    host: str = 'localhost'
    port: int = 5432
    database: str = ''
    schema: str = 'public'
    username: str = 'postgres'
    password: str = ''         # 평문 저장. 운영 환경에서 주의
    service: str = ''          # pg_service.conf 연동용 (선택)

    def as_dsn(self):
        parts = [f'host={self.host}', f'port={self.port}']
        if self.database:
            parts.append(f'dbname={self.database}')
        if self.username:
            parts.append(f'user={self.username}')
        if self.password:
            parts.append(f'password={self.password}')
        return ' '.join(parts)

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d.get(k, v) for k, v in cls().as_dict().items()})


def save_profile(name, profile):
    """QgsSettings에 프로파일 저장."""
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    base = f'{SETTINGS_PREFIX}/profiles/{name}'
    for k, v in profile.as_dict().items():
        s.setValue(f'{base}/{k}', v)


def load_profile(name):
    """QgsSettings에서 프로파일 로드 (없으면 기본값)."""
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    base = f'{SETTINGS_PREFIX}/profiles/{name}'
    if not s.contains(f'{base}/host'):
        return PGProfile()
    d = {}
    for k in PGProfile().as_dict().keys():
        d[k] = s.value(f'{base}/{k}', '')
    # port 정수 변환
    try:
        d['port'] = int(d.get('port') or 5432)
    except (ValueError, TypeError):
        d['port'] = 5432
    return PGProfile.from_dict(d)


def list_profiles():
    """저장된 프로파일 이름 목록."""
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    s.beginGroup(f'{SETTINGS_PREFIX}/profiles')
    names = s.childGroups()
    s.endGroup()
    if DEFAULT_PROFILE not in names:
        names = [DEFAULT_PROFILE] + names
    return names


def delete_profile(name):
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    s.remove(f'{SETTINGS_PREFIX}/profiles/{name}')


def test_connection(profile, timeout_s=5):
    """연결 테스트 — psycopg2 사용. 성공 시 (True, 서버 버전 문자열).

    psycopg2 없으면 QGIS 내장 QgsDataSourceUri + ogr로 폴백 가능하나
    여기서는 psycopg2 기본 가정 (QGIS 번들 환경에 포함).
    """
    try:
        import psycopg2
    except ImportError:
        return False, 'psycopg2 미설치'
    conn = None
    try:
        conn = psycopg2.connect(
            host=profile.host,
            port=profile.port,
            dbname=profile.database or 'postgres',
            user=profile.username,
            password=profile.password or None,
            connect_timeout=timeout_s,
        )
        with conn.cursor() as cur:
            cur.execute('SELECT version()')
            ver = cur.fetchone()[0]
            # PostGIS 버전 체크
            postgis_ver = None
            try:
                cur.execute("SELECT postgis_full_version()")
                postgis_ver = cur.fetchone()[0]
            except Exception:
                pass
        msg = ver[:80]
        if postgis_ver:
            msg += f'\nPostGIS: {postgis_ver[:80]}'
        else:
            msg += '\n(PostGIS 확장 미설치)'
        return True, msg
    except Exception as e:
        return False, str(e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def list_schemas(profile):
    """DB에 있는 schema 목록. 선택 UI용."""
    import psycopg2
    with psycopg2.connect(
            host=profile.host, port=profile.port,
            dbname=profile.database, user=profile.username,
            password=profile.password or None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name NOT LIKE 'pg_%%'
                  AND schema_name != 'information_schema'
                ORDER BY 1
            """)
            return [r[0] for r in cur.fetchall()]

"""서버 API 클라이언트 — 대전 플러그인 ↔ 서울 서버 (HTTPS 배치 동기화).

대전은 PostGIS/MinIO에 직접 접속하지 않는다. 모든 통신은 Funnel HTTPS 한 줄기:
- 경계 벡터 제출 / COG 등록  → REST API (`/api`, Bearer 토큰)
- COG·원본 이미지 업로드     → S3 (MinIO, `/s3` endpoint, 액세스키)

수정요청(마크업)은 플러그인이 다루지 않는다 — 작업자가 웹 화면에서 요청을 보고
QGIS 로 경계만 수정·제출하면, 반영/반려 처리는 웹에서 한다.

requests/boto3 는 지연 import — 미설치 환경에서도 플러그인 자체는 로드되게.
"""
from dataclasses import dataclass, asdict


SETTINGS_PREFIX = 'gis_scan_tools/server'

# 정식 도메인 (Caddy HTTPS 종단, Let's Encrypt 자동 갱신).
DEFAULT_BASE_URL = 'https://www.kosisgis.kr'
# 구 주소 — QSettings 에 저장돼 있으면 load_config() 가 정식 도메인으로 자동 치환.
LEGACY_BASE_URLS = ('https://gis-hq.tail3b9b19.ts.net',)
DEFAULT_BUCKET = 'gis-scan'
HTTP_TIMEOUT = 30          # 일반 API 호출
UPLOAD_TIMEOUT = 600       # 대용량 업로드


@dataclass
class ServerConfig:
    """서버 연결 설정 — QSettings 저장."""
    base_url: str = DEFAULT_BASE_URL      # 예: https://www.kosisgis.kr
    api_token: str = ''                   # /api Bearer 토큰
    s3_access_key: str = ''               # MinIO write 키
    s3_secret_key: str = ''
    bucket: str = DEFAULT_BUCKET

    @property
    def api_base(self):
        return self.base_url.rstrip('/') + '/api'

    @property
    def s3_endpoint(self):
        return self.base_url.rstrip('/') + '/s3'

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d.get(k, v) for k, v in cls().as_dict().items()})


def save_config(cfg):
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    for k, v in cfg.as_dict().items():
        s.setValue(f'{SETTINGS_PREFIX}/{k}', v)


def load_config():
    """QSettings 에서 로드. 없으면 기본값.

    구 서버 주소(Tailscale Funnel)가 저장돼 있으면 정식 도메인으로 자동 치환 후
    다시 저장 — 작업자 PC 에서 수동으로 주소를 바꿀 필요 없음.
    """
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    d = {}
    for k, default in ServerConfig().as_dict().items():
        d[k] = s.value(f'{SETTINGS_PREFIX}/{k}', default)
    cfg = ServerConfig.from_dict(d)
    if cfg.base_url.rstrip('/') in LEGACY_BASE_URLS:
        cfg.base_url = DEFAULT_BASE_URL
        save_config(cfg)
    return cfg


# ----- 내부 헬퍼 -----

def _session(cfg):
    """Bearer 인증이 붙은 requests 세션."""
    import requests
    sess = requests.Session()
    if cfg.api_token:
        sess.headers['Authorization'] = f'Bearer {cfg.api_token}'
    return sess


def _s3_client(cfg):
    """MinIO 용 boto3 S3 클라이언트 (path-style, SigV4).

    nginx 가 `/s3/` prefix 를 rewrite 로 제거하므로 endpoint_url 에는 `/s3` 를
    넣지 않고, 서명 이후·전송 직전(before-send) 훅으로 경로 앞에 `/s3` 를
    덧붙인다. 그래야 nginx 가 다시 제거했을 때 MinIO 가 보는 경로 = 서명 대상
    경로가 일치한다.
    """
    import boto3
    from botocore.config import Config
    host = cfg.base_url.rstrip('/')
    client = boto3.client(
        's3',
        endpoint_url=host,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name='us-east-1',
        config=Config(s3={'addressing_style': 'path'},
                      signature_version='s3v4',
                      retries={'max_attempts': 3}),
    )

    def _add_prefix(request, **_kwargs):
        request.url = request.url.replace(host + '/', host + '/s3/', 1)

    client.meta.events.register('before-send.s3', _add_prefix)
    return client


# ----- 연결 테스트 -----

def test_connection(cfg, timeout_s=10):
    """API 도달성 + 인증 확인. 성공 시 (True, 메시지).

    GET /api/admins 로 검증 — read-only 라 부작용 없음.
    """
    try:
        import requests  # noqa: F401
    except ImportError:
        return False, 'requests 미설치 (pip install requests)'
    try:
        sess = _session(cfg)
        r = sess.get(f'{cfg.api_base}/admins', timeout=timeout_s)
        if r.status_code == 401:
            return False, '인증 실패 — API 토큰 확인'
        r.raise_for_status()
        try:
            n = len(r.json())
        except Exception:
            n = '?'
        return True, f'연결 성공 — 검수 대상 {n}건'
    except Exception as e:
        return False, str(e)


# ----- 경계 제출 -----

def submit_boundary(cfg, geojson, updated_by=''):
    """경계 GeoJSON(FeatureCollection)을 서버에 upsert.

    PUT /api/boundary — adm_cd+ri_cd 기준 업서트. (affected_count, 메시지) 반환.
    경계 데이터만 다룬다 — 수정요청(마크업) 처리는 웹에서.
    """
    sess = _session(cfg)
    params = {'updated_by': updated_by} if updated_by else None
    r = sess.put(f'{cfg.api_base}/boundary', json=geojson, params=params,
                 timeout=HTTP_TIMEOUT)
    if r.status_code == 401:
        raise RuntimeError('인증 실패 — API 토큰 확인')
    r.raise_for_status()
    body = r.json() if r.content else {}
    affected = body.get('affected', body.get('count', 0))
    msg = body.get('message', f'{affected}건 반영')
    return affected, msg


# ----- COG 등록 -----

def register_cog(cfg, adm_cd, s3_key, bounds=None, width=None, height=None):
    """업로드한 COG 를 cog_catalog 에 등록.

    POST /api/cog. bounds 는 [xmin,ymin,xmax,ymax] (EPSG:5179).
    """
    sess = _session(cfg)
    payload = {'adm_cd': adm_cd, 's3_key': s3_key}
    if bounds is not None:
        payload['bounds'] = list(bounds)
    if width is not None:
        payload['width'] = int(width)
    if height is not None:
        payload['height'] = int(height)
    r = sess.post(f'{cfg.api_base}/cog', json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code == 401:
        raise RuntimeError('인증 실패 — API 토큰 확인')
    r.raise_for_status()
    return r.json() if r.content else {}


# ----- S3 업로드 -----

def upload_s3(cfg, local_path, key, content_type=None):
    """로컬 파일을 MinIO 버킷에 업로드. 업로드된 객체 key 반환.

    key 레이아웃 예: cog/{시도}/{시군구}/{admin}.tif
    """
    import os
    if not os.path.isfile(local_path):
        raise FileNotFoundError(local_path)
    client = _s3_client(cfg)
    extra = {}
    if content_type:
        extra['ContentType'] = content_type
    client.upload_file(local_path, cfg.bucket, key,
                       ExtraArgs=extra or None)
    return key


def check_s3(cfg):
    """S3 자격증명/버킷 도달성 확인. (True, 메시지)."""
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False, 'boto3 미설치 (pip install boto3)'
    try:
        client = _s3_client(cfg)
        client.head_bucket(Bucket=cfg.bucket)
        return True, f'S3 OK — 버킷 "{cfg.bucket}"'
    except Exception as e:
        return False, str(e)

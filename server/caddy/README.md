# kosisgis.kr HTTPS 종단 (Caddy)

검수 웹의 정식 공개 주소 **https://www.kosisgis.kr** 을 담당하는 HTTPS 종단 계층.
기존 compose 스택(nginx `gis-web-1`, 127.0.0.1:8080)과 완전히 분리된 별도 컨테이너로 동작한다.

```
인터넷 → 공유기(ASUS RT-AX53U, 443 포워딩)
       → 192.168.0.105:443  kosisgis-https (Caddy, Let's Encrypt 자동 발급/갱신)
       → 127.0.0.1:8080     gis-web-1 (기존 nginx 컨테이너)
```

## 환경 제약 (왜 이렇게 구성했나)

- **80 포트 사용 불가** — 공유기에서 80 포트는 다른 서비스(geoband, 192.168.0.104)로
  포워딩되어 있음. 따라서 HTTP-01 챌린지 불가 → 인증서는 **TLS-ALPN-01**(443만 사용)로 발급.
  `http://` 접속은 안 되므로 안내 시 반드시 `https://` 를 명시할 것.
- **443 바인딩은 사내망 IP(192.168.0.105)에만** — 0.0.0.0:443 으로 바인딩하면
  tailscaled(100.106.19.100:443, Funnel)와 충돌함.
- 공인 IP: 180.71.194.230 (SK브로드밴드 고정 IP)
- 도메인: kosisgis.kr (가비아) — A 레코드는 **www 만** 등록됨 (루트 @ 없음)

## 배포

운영 서버의 설정 경로는 `/srv/gis/caddy/Caddyfile` (이 디렉터리의 Caddyfile 을 복사/동기화).

```bash
# 설정 반영
cp server/caddy/Caddyfile /srv/gis/caddy/Caddyfile

# 컨테이너 기동 (최초 1회)
docker run -d --name kosisgis-https --network host --restart unless-stopped \
  -v /srv/gis/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v /srv/gis/caddy/data:/data \
  -v /srv/gis/caddy/config:/config \
  caddy:2.10

# 설정 변경 후 재적용
docker exec kosisgis-https caddy reload --config /etc/caddy/Caddyfile
```

- 인증서/키는 `/srv/gis/caddy/data` 에 저장되며 Caddy 가 만료 전 자동 갱신한다 (별도 작업 불필요).
- 컨테이너는 compose 스택과 무관하므로 `docker compose up/down` 의 영향을 받지 않는다.

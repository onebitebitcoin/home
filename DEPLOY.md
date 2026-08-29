# DEPLOY.md — home 최초 배포 런북

**이 문서를 읽는 대상은 배포 서버에 접속한 사람 또는 Claude다.** 위에서부터
순서대로 실행하고, 각 단계 끝의 "확인"이 기대한 값을 내지 않으면 **다음 단계로
넘어가지 말고 멈춰라.** 되돌리는 법은 마지막 절에 있다.

같은 서버에 `btc-daily-web`·`ai-daily-web`·`stack-health`·`meetup-web`이 이미
돌고 있다는 것을 전제로 쓴 문서다. **그쪽을 건드리지 않는 것이 이 배포의
제약이다.**

| | 값 |
|---|---|
| 도메인 | `onebitebitcoin.com` (apex) |
| 사람이 보는 주소 | `https://onebitebitcoin.com/` |
| 컨테이너 노출 포트 | `127.0.0.1:8022` |
| compose 프로젝트명 | `home` (디렉토리명) |
| 서비스 | `web` (python stdlib http.server 하나뿐) |

이 앱은 표준 라이브러리만 쓴다. DB는 파일 하나(sqlite)이고 `notices` 볼륨에
들어간다. 빌드 단계도, 마이그레이션 명령도 없다.

---

## 0. 시작 전 — 사람이 먼저 해야 할 두 가지

**Claude가 대신할 수 없다. 둘 다 끝난 것을 확인하고 1절로 간다.**

### 0-1. Cloudflare에 apex 레코드 추가

이 글을 쓰는 시점에 `onebitebitcoin.com`(apex)에는 **DNS 레코드가 없다.**
서브도메인(`daily`·`fee`·`meet` …)만 있다.

Cloudflare 대시보드에서 `onebitebitcoin.com` 존에 이 서버를 가리키는
**A 레코드**를 추가한다. 이름은 `@`, 프록시는 켠다(주황 구름).

### 0-2. SSL/TLS 모드 확인

**`Full (strict)`여야 한다.** `Flexible`이면 Cloudflare가 서버로 평문 HTTP를
보내는데, 아래 vhost의 `:80 → :443` 리다이렉트와 물려 무한 루프가 난다.
서버에서 고칠 수 없는 문제다.

같은 존의 다른 서브도메인이 이미 정상이면 대개 이미 맞춰져 있다.

### 확인

```bash
dig +short onebitebitcoin.com          # Cloudflare IP 두 개가 나와야 한다
curl -s -o /dev/null -w "%{http_code}\n" http://onebitebitcoin.com/

# 8022 가 비어 있는지. 뭔가 물려 있으면 멈추고 사람에게 물어라
sudo lsof -iTCP:8022 -sTCP:LISTEN || echo "8022 비어 있음 (정상)"

# 형제 서비스가 살아 있는지 — 이 배포가 끝난 뒤에도 살아 있어야 한다
for u in https://daily.onebitebitcoin.com/ https://daily.onebitecoder.com/ \
         https://fee.onebitebitcoin.com/; do
  printf "%-40s " "$u"; curl -s -o /dev/null -w "%{http_code}\n" "$u"
done
```

`dig`가 빈 값을 내면 0-1이 아직 안 끝난 것이다. 여기서 멈춘다.

---

## 1. 코드 배치

```bash
cd /home/measly
git clone https://github.com/onebitebitcoin/home.git   # 이미 있으면 git pull
cd /home/measly/home
git log --oneline -1
```

> `/home/measly`는 형제 프로젝트들이 있는 자리다. 다르면
> `ls -d /home/measly/ai-daily-web`으로 실제 위치를 먼저 확인하고 그 옆에
> 붙여라 — 이 문서의 나머지 경로도 같이 바꿔야 한다.

**확인** — 배포 파일이 들어온 커밋인지 본다:

```bash
ls Dockerfile docker-compose.yml .env.example deploy/nginx/
```

---

## 2. `.env` 작성

```bash
cp .env.example .env
```

`.env`를 열어 아래 넷을 채운다.

| 키 | 값 | 비고 |
|---|---|---|
| `ADMIN_PASSWORD` | 사람이 정한 값 | 비면 compose가 기동을 거부한다 |
| `SECRET_KEY` | `openssl rand -hex 32` | 비면 재배포마다 관리자 로그인이 풀린다 |
| `WEB_PORT` | `8022` | `.env.example` 기본값 그대로 |
| `DOMAIN` | `onebitebitcoin.com` | `.env.example` 기본값 그대로 |

> **`ADMIN_PASSWORD`가 이 배포의 유일한 잠금이다.** 화면의 관리자 버튼은
> 기기별 표식으로 감춰져 있지만 `/api/notices/auth`는 누구에게나 열려 있다.
> 코드 기본값은 `0000`이라, 이 값을 비워둔 채 뜨는 것이 가장 피해야 할
> 실패다. `docker-compose.yml`이 `${ADMIN_PASSWORD:?}`로 막아 두었다.

**확인** — 값이 다 찼는지만 본다. 값 자체는 출력하지 마라:

```bash
sed -n 's/^\([A-Z_]*\)=\(.\+\)/\1 OK/p' .env
```

`ADMIN_PASSWORD`·`SECRET_KEY`·`WEB_PORT`·`DOMAIN` 네 줄이 `OK`로 나와야 한다.

---

## 3. 컨테이너 기동

```bash
docker compose up -d --build
docker compose ps
```

`.env`에 `ADMIN_PASSWORD`나 `SECRET_KEY`가 비어 있으면 여기서 즉시 멈춘다.
의도된 설계다 — 2절로 돌아간다.

**확인**

```bash
curl -s -o /dev/null -w "home: %{http_code}\n"   localhost:8022/
curl -s -o /dev/null -w "notice: %{http_code}\n" localhost:8022/notice
curl -s localhost:8022/api/notices               # {"ok":true,"data":{"items":[],...}}

# 아이콘·CSS·JS 를 실제로 받아본다. HTML 안의 경로만 grep 하면 파일이 없어도 통과한다
for p in $(curl -s localhost:8022/ | grep -o 'assets/[a-z0-9/._-]*'); do
  printf "%-40s " "/$p"; curl -s -o /dev/null -w "%{http_code}\n" "localhost:8022/$p"
done
#   → 모두 200

# 시세 프록시(업스트림 fee.onebitebitcoin.com)가 컨테이너에서 나가는지
curl -s -o /dev/null -w "kimp: %{http_code}\n" localhost:8022/api/v1/market/kimp/live
```

`web`은 `127.0.0.1`에만 바인딩된다. 공인 IP로 8022가 열려 있으면 안 된다:

```bash
sudo lsof -iTCP:8022 -sTCP:LISTEN    # 127.0.0.1:8022 여야 한다. *:8022 면 잘못됐다
```

---

## 4. 인그레스 + TLS (2단계)

호스트 nginx(`/etc/nginx`)가 80/443과 인증서를 소유한다. 컨테이너 안에는
nginx가 없다 — `server.py`가 정적 서빙과 API를 다 한다.

인증서가 없는 상태로 `:443` 블록을 넣으면 `nginx -t`가 깨지므로 반드시 두 번에
나눠 올린다.

### 4-1. `:80` 전용 vhost 먼저

```bash
sudo cp deploy/nginx/onebitebitcoin.com.bootstrap.conf \
        /etc/nginx/sites-available/onebitebitcoin.com
sudo ln -sf /etc/nginx/sites-available/onebitebitcoin.com \
            /etc/nginx/sites-enabled/onebitebitcoin.com
sudo nginx -t && sudo systemctl reload nginx
```

**확인**: `curl -s -o /dev/null -w "%{http_code}\n" http://onebitebitcoin.com/.well-known/acme-challenge/probe` → `404`
(200이 아니라 404가 정상이다. nginx가 그 경로를 webroot로 넘겼다는 뜻)

### 4-2. 인증서 발급

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt \
     --cert-name onebitebitcoin.com -d onebitebitcoin.com
```

> **기존 인증서에 `--expand`로 붙이지 마라.** SAN 목록을 잘못 넘기면
> `daily.onebitebitcoin.com` 같은 형제 도메인이 갱신에서 조용히 빠진다.
> `--cert-name`으로 별도 인증서를 만드는 게 이 서버의 관례다.

`/var/www/letsencrypt`가 없으면 만들고 4-1부터 다시 한다:
`sudo mkdir -p /var/www/letsencrypt`

**확인**: `sudo certbot certificates | grep -A3 onebitebitcoin.com`

### 4-3. TLS 포함 최종 vhost로 교체

```bash
sudo cp deploy/nginx/onebitebitcoin.com.conf \
        /etc/nginx/sites-available/onebitebitcoin.com
sudo nginx -t && sudo systemctl reload nginx
```

**확인**

```bash
curl -sIL https://onebitebitcoin.com/ | grep -iE '^(HTTP|location)'
#   → 200. 중간에 http:// 가 끼면 Cloudflare SSL/TLS 모드가 Flexible 이다(0-2)

curl -s https://onebitebitcoin.com/api/notices
for p in $(curl -s https://onebitebitcoin.com/ | grep -o 'assets/[a-z0-9/._-]*'); do
  printf "%-40s " "/$p"; curl -s -o /dev/null -w "%{http_code}\n" "https://onebitebitcoin.com/$p"
done
#   → 모두 200

# 보안 헤더가 실려 나가는지
curl -sI https://onebitebitcoin.com/notice | grep -iE 'content-security|x-content-type|referrer'
```

갱신은 certbot이 등록한 스케줄 작업이 처리한다.

---

## 5. 배포 후 확인 (브라우저)

1. `https://onebitebitcoin.com/` — 앱 타일 8개, 시세와 김치 프리미엄이 뜬다
2. `https://onebitebitcoin.com/notice` — **관리자 버튼이 보이면 안 된다**
3. `https://onebitebitcoin.com/notice?admin=<문구>` — 버튼이 나타나고 주소창이 정리된다
4. `관리자` → `.env`에 넣은 비밀번호 → `글쓰기`가 나타난다
5. 틀린 비밀번호를 10번 넘게 넣으면 429가 나온다 (레이트 리밋)

`<문구>`는 `assets/notice.js`의 `ADMIN_GATE_HASH`에 대응하는 값이다.
소스에는 해시만 있으므로 문구를 잃어버리면 새로 정해 해시를 갈아야 한다.

5번이 IP별로 걸리는지 확인하려면 다른 회선(모바일 데이터 등)에서 한 번 더
시도해 본다. 한 IP가 막혔는데 다른 IP도 같이 막히면 `TRUST_PROXY=1`이 안 먹은
것이다 — `docker compose exec web env | grep TRUST_PROXY`로 확인한다.

---

## 6. 갱신 배포 (다음부터)

```bash
cd /home/measly/home && git pull && docker compose up -d --build
curl -s -o /dev/null -w "%{http_code}\n" localhost:8022/
```

공지 글은 `notices` 볼륨에 있으므로 재배포해도 남는다. nginx vhost는 이
저장소 파일이 바뀐 경우에만 다시 복사한다.

---

## 7. 되돌리기

| 상황 | 조치 |
|---|---|
| 컨테이너만 문제 | `docker compose down && git checkout <이전 커밋> && docker compose up -d --build` |
| vhost가 nginx를 깨뜨림 | `sudo rm /etc/nginx/sites-enabled/onebitebitcoin.com && sudo nginx -t && sudo systemctl reload nginx` — 형제 도메인은 별도 vhost라 영향받지 않는다 |
| 공지를 통째로 비워야 함 | `docker compose down -v` (**글과 댓글이 지워진다**) |
| 관리자 비밀번호 변경 | `.env`의 `ADMIN_PASSWORD`를 고치고 `docker compose up -d`. 발급된 토큰은 2시간 뒤 만료되며, 즉시 끊으려면 `SECRET_KEY`도 같이 바꾼다 |
| 전체 철수 | `docker compose down -v` + vhost 심볼릭 링크 제거 + `sudo certbot delete --cert-name onebitebitcoin.com` |

공지 백업:

```bash
docker compose exec web python3 -c "import sqlite3,sys; sqlite3.connect('/data/notices.db').iterdump() and None" >/dev/null
docker compose cp web:/data/notices.db ./notices-backup-$(date +%F).db
```

---

## 이 배포에서 하지 말아야 할 것

- **형제 프로젝트의 vhost·컨테이너·인증서를 건드리지 마라.** 같은 서버에
  있지만 완전히 별개다. `certbot --expand`로 인증서를 합치는 것도 여기 포함된다.
- **`WEB_PORT`를 8020·8021·8018·8019·8000으로 바꾸지 마라.** 형제 서비스가 쓰고 있다.
- **`web` 서비스를 `0.0.0.0`에 바인딩하지 마라.** 공인 IP:8022로 TLS와
  Cloudflare를 우회한 평문 직결이 뚫린다.
- **`.env` 값을 로그나 터미널에 출력하지 마라.** 있는지 없는지만 확인한다.
- **`ADMIN_PASSWORD`를 비워둔 채 띄우지 마라.** compose가 막아 두었지만,
  그 가드를 우회해 환경변수를 직접 넘기면 코드 기본값 `0000`으로 뜬다.

---

## 아직 안 한 것

- **`www.onebitebitcoin.com`**: DNS 레코드는 있는데 오리진이 없어
  Cloudflare 오류 1016이 난다. 이 배포는 apex만 다룬다. www도 여기로 붙이려면
  Cloudflare에서 apex와 같은 곳을 가리키게 하고, vhost의 `server_name`과
  certbot의 `-d`에 `www.onebitebitcoin.com`을 추가해야 한다.
- **백업 자동화**: 위 `docker compose cp`를 손으로 돌리는 상태다.

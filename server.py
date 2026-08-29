#!/usr/bin/env python3
"""홈 대시보드 서버: 정적 파일 + fee.onebitebitcoin.com API 프록시(CORS 우회) + 공지사항 API"""
import http.server
import json
import os
import urllib.request

import notice_api
import notice_db

UPSTREAM = "https://fee.onebitebitcoin.com"
# 컨테이너에서는 compose 가 넘긴 값을 쓴다. 로컬은 기본값 그대로.
PORT = int(os.environ.get("PORT", "8899"))


def _is_notice_api_path(path: str) -> bool:
    """'/api/notices' 자체이거나 그 하위 경로일 때만 True.

    단순 startswith('/api/notices') 는 '/api/noticesomething' 같은 경로까지
    잘못 붙잡기 때문에 경계를 명시적으로 확인한다.
    """
    p = path.split('?', 1)[0]
    return p == '/api/notices' or p.startswith('/api/notices/')


# 예전에 /pos/ 에 PWA 가 있었다. 그 서비스 워커가 아직 등록된 기기는 서버가
# 404 를 줘도 캐시된 옛 화면을 계속 띄운다 - 네트워크를 아예 타지 않기 때문이다.
# 브라우저는 스코프 안의 페이지로 이동할 때 워커 스크립트 자체는 따로 받아
# 갱신을 확인하므로, 그 자리에 자기를 지우는 워커를 놓아두면 방문 한 번에
# 스스로 풀린다. 사용자가 캐시를 지울 필요가 없다.
POS_KILLSWITCH_SW = """// /pos/ PWA 회수용. 등록된 서비스 워커를 스스로 해제한다.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      await caches.delete(key);
    }
    await self.registration.unregister();
    // 열려 있는 탭을 새로 고쳐 바로 새 사이트로 보낸다.
    for (const client of await self.clients.matchAll({ type: 'window' })) {
      client.navigate(client.url);
    }
  })());
});
""".encode("utf-8")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/pos/service-worker.js":
            self._send_pos_killswitch()
        elif self._is_legacy_pos_path():
            # 워커가 죽은 뒤에야 여기까지 온다. 302 인 이유는 나중에 /pos 를
            # 다시 쓸 수도 있어서다 - 301 은 브라우저에 오래 박힌다.
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif _is_notice_api_path(self.path):
            notice_api.handle(self, "GET")
        elif self.path.startswith("/api/"):
            self.proxy()
        else:
            self._route_notice_page()
            self._route_root_favicon()
            super().do_GET()

    def _is_legacy_pos_path(self) -> bool:
        p = self.path.split("?", 1)[0]
        return p == "/pos" or p.startswith("/pos/")

    def _send_pos_killswitch(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(POS_KILLSWITCH_SW)))
        self.end_headers()
        self.wfile.write(POS_KILLSWITCH_SW)

    def do_HEAD(self):
        # do_GET 만 고치면 HEAD 가 옛 경로를 몰라 404 를 낸다. 브라우저는 워커
        # 스크립트를 GET 으로 받지만, 헤더만 찍어보는 점검 도구가 엇갈린 답을
        # 받으면 배포 확인이 헷갈린다.
        if self.path.split("?", 1)[0] == "/pos/service-worker.js":
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(POS_KILLSWITCH_SW)))
            self.end_headers()
        elif self._is_legacy_pos_path():
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._route_notice_page()
            self._route_root_favicon()
            super().do_HEAD()

    def do_POST(self):
        if _is_notice_api_path(self.path):
            notice_api.handle(self, "POST")
        else:
            self._method_not_allowed()

    def do_PUT(self):
        if _is_notice_api_path(self.path):
            notice_api.handle(self, "PUT")
        else:
            self._method_not_allowed()

    def do_DELETE(self):
        if _is_notice_api_path(self.path):
            notice_api.handle(self, "DELETE")
        else:
            self._method_not_allowed()

    def _route_notice_page(self):
        """공지사항 관련 경로를 실제 정적 HTML 파일 경로로 바꾼다.

        'new' 는 숫자가 아니라서 순서가 결과에 영향을 주진 않지만, 명시적으로
        정수 id 판정보다 먼저 검사한다. 표에 없는 /notice/... 경로는 self.path 를
        그대로 둬서 정적 파일 탐색이 자연스럽게 404 를 내도록 한다.
        """
        path = self.path.split('?', 1)[0]
        if path in ('/notice', '/notice/'):
            self.path = '/notice.html'
        elif path == '/notice/new':
            self.path = '/notice-form.html'
        elif path.startswith('/notice/'):
            rest = path[len('/notice/'):]
            if rest.endswith('/edit') and rest[:-len('/edit')].isdigit():
                self.path = '/notice-form.html'
            elif rest.isdigit():
                self.path = '/notice-detail.html'
            # 그 외는 그대로 둔다 (정적 파일 없음 -> 404)

    def _route_root_favicon(self):
        """루트 /favicon.ico 요청을 아이콘 파일로 잇는다.

        즐겨찾기, RSS 리더, 일부 크롤러는 <link rel="icon"> 을 읽지 않고 루트를
        곧장 때린다. 아이콘 원본은 assets/icons 아래 모아 두고 싶으므로 루트에
        복사본을 두는 대신 경로만 바꿔준다.
        """
        if self.path.split('?', 1)[0] == '/favicon.ico':
            self.path = '/assets/icons/favicon.ico'

    def _method_not_allowed(self):
        payload = json.dumps(
            {'ok': False, 'data': None, 'error': '허용되지 않은 메서드입니다'},
            ensure_ascii=False,
        ).encode('utf-8')
        self.send_response(405)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def proxy(self):
        try:
            req = urllib.request.Request(UPSTREAM + self.path, headers={"User-Agent": "home-dashboard"})
            with urllib.request.urlopen(req, timeout=10) as res:
                body = res.read()
                self.send_response(res.status)
                self.send_header("Content-Type", res.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _csp_policy(self) -> str:
        """/notice, /assets/notice 경로는 인라인 스크립트/스타일을 쓰지 않으므로 엄격판을 쓴다.
        index.html 은 인라인 CSS/JS 를 쓰기 때문에 나머지 경로는 완화판을 유지한다.

        주의: 페이지 라우팅(_route_notice_page)이 self.path 를 이미
        '/notice-detail.html' 등으로 바꾼 뒤에 end_headers 가 불린다. 바뀐 경로도
        여전히 '/notice' 로 시작하므로 우연히 엄격판이 계속 적용되는데, 이건
        의도한 동작이다 (공지사항 페이지는 항상 엄격판을 받아야 한다).
        """
        if self.path.startswith("/notice") or self.path.startswith("/assets/notice"):
            return (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
        return (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )

    def end_headers(self):
        # 로고/아이콘을 고쳐도 브라우저가 옛 파일을 물고 있어서 반영이 안 되던
        # 문제 때문에 넣었다. no-store 가 아니라 no-cache 인 이유: no-cache 는
        # "쓰기 전에 매번 서버에 물어보라"는 뜻이라 SimpleHTTPRequestHandler 의
        # If-Modified-Since 처리가 304 로 답한다. 최신성은 같고 전송량만 준다.
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # proxy() 응답에도 end_headers 가 그대로 불리므로 이 헤더들도 함께 실린다.
        self.send_header("Content-Security-Policy", self._csp_policy())
        super().end_headers()

    def log_message(self, *args):
        pass  # ponytail: 조용한 로그, 필요하면 삭제


if __name__ == "__main__":
    notice_db.init_db()
    print(f"http://localhost:{PORT}")
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()

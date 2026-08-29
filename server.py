#!/usr/bin/env python3
"""홈 대시보드 서버: 정적 파일 + fee.onebitebitcoin.com API 프록시(CORS 우회) + 공지사항 API"""
import http.server
import json
import urllib.request

import notice_api
import notice_db

UPSTREAM = "https://fee.onebitebitcoin.com"
PORT = 8899


def _is_notice_api_path(path: str) -> bool:
    """'/api/notices' 자체이거나 그 하위 경로일 때만 True.

    단순 startswith('/api/notices') 는 '/api/noticesomething' 같은 경로까지
    잘못 붙잡기 때문에 경계를 명시적으로 확인한다.
    """
    p = path.split('?', 1)[0]
    return p == '/api/notices' or p.startswith('/api/notices/')


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if _is_notice_api_path(self.path):
            notice_api.handle(self, "GET")
        elif self.path.startswith("/api/"):
            self.proxy()
        else:
            self._route_notice_page()
            super().do_GET()

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
        # 로고/아이콘을 고쳐도 브라우저가 옛 파일을 물고 있어서 반영이 안 된다.
        # 로컬 개발 서버라 전부 껐다.
        self.send_header("Cache-Control", "no-store, must-revalidate")
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

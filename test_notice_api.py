#!/usr/bin/env python3
"""공지사항 API 통합 테스트.

server.py 의 Handler 를 임시 sqlite DB 로 띄우고 실제 HTTP 요청을 보내서 검증한다.
stdlib unittest 만 쓴다 (pytest 미설치 환경).

주의: notice_db.DB_PATH 와 notice_auth.ADMIN_PASSWORD 는 각 모듈이 import 되는
시점에 환경변수를 읽어서 확정되는 값이다. 그래서 이 파일 맨 위, `import notice_db`
보다 먼저 os.environ 을 설정해야 한다 (setUpModule 은 이미 import 가 끝난 뒤에
실행되므로 거기서 설정하면 늦는다).
"""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

# --- 여기서부터 import 전 환경 설정 (순서 중요) -----------------------------
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(suffix='.db', prefix='notice_test_')
os.close(_TMP_DB_FD)
os.environ['NOTICE_DB'] = _TMP_DB_PATH
_ADMIN_PW = 'test-only-admin-pw-0000'
os.environ['ADMIN_PASSWORD'] = _ADMIN_PW

import notice_auth  # noqa: E402  (환경변수 설정 뒤에 import 해야 한다)
import notice_db  # noqa: E402
import server  # noqa: E402

# --- 입력 제한 상수 (계약서 그대로) ------------------------------------------
TITLE_MAX = 200
BODY_MAX = 10000
NICK_MAX = 20
COMMENT_BODY_MAX = 1000
PASSWORD_MIN = 4
PASSWORD_MAX = 64


def setUpModule():
    notice_db.init_db()


def tearDownModule():
    for suffix in ('', '-wal', '-shm'):
        path = _TMP_DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)


def _looks_like_ip_key(key) -> bool:
    """dict 키가 IP 주소처럼 생겼는지 대충 판별한다 (아래 폴백 경로에서만 쓴다)."""
    if isinstance(key, str):
        return '.' in key or ':' in key
    if isinstance(key, tuple):
        return any(_looks_like_ip_key(k) for k in key)
    return False


def _clear_if_ip_keyed(value, depth=0):
    """value 가 IP-keyed dict 면 비우고, dict-of-dict 형태면 한 단계만 더 들어가본다."""
    if not isinstance(value, dict) or not value:
        return
    if all(_looks_like_ip_key(k) for k in value.keys()):
        value.clear()
        return
    if depth == 0:
        for v in value.values():
            _clear_if_ip_keyed(v, depth=1)


def _reset_rate_limit_counters():
    """레이트 리밋 카운터를 리셋한다.

    notice_auth.py 가 정확히 이 용도로 notice_auth.reset_rate_limits() 를
    공개해 두었으므로 그걸 우선 쓴다. auth/댓글 작성/댓글 삭제 레이트 리밋이
    전부 같은 notice_auth._BUCKETS 저장소를 공유하기 때문에 한 번만 호출하면
    셋 다 초기화된다. 혹시 다른 이름/모듈로 바뀌어 있으면(테스트를 구현과
    동시에 작성했다) 폴백으로 IP 모양 키를 가진 dict 를 찾아 비운다.
    """
    reset_fn = getattr(notice_auth, 'reset_rate_limits', None)
    if callable(reset_fn):
        reset_fn()
        return
    for mod in (notice_auth, server):
        for name in dir(mod):
            if name.startswith('__'):
                continue
            try:
                value = getattr(mod, name)
            except Exception:
                continue
            _clear_if_ip_keyed(value)


class _BaseAPITestCase(unittest.TestCase):
    """서버를 클래스당 한 번 띄우고 공유하는 베이스 테스트케이스."""

    @classmethod
    def setUpClass(cls):
        cls._token_cache = {}
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        # 레이트 리밋 카운터는 프로세스(모듈) 전역 상태라 테스트 클래스가
        # 달라도 공유된다. 매 테스트 시작 전에 리셋해서 서로 간섭하지 않게 한다.
        _reset_rate_limit_counters()

    def req(self, method, path, body=None, token=None, headers=None, raw_body=None):
        """HTTP 요청 하나를 보내고 (status, 파싱된 JSON 또는 None) 을 반환한다."""
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            hdrs = dict(headers or {})
            data = None
            if raw_body is not None:
                data = raw_body
            elif body is not None:
                data = json.dumps(body).encode('utf-8')
                hdrs.setdefault('Content-Type', 'application/json')
            if token is not None:
                hdrs['Authorization'] = f'Bearer {token}'
            conn.request(method, path, body=data, headers=hdrs)
            res = conn.getresponse()
            raw = res.read()
            status = res.status
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            return status, parsed
        finally:
            conn.close()

    def admin_token(self):
        """클래스당 한 번만 로그인해서 캐시한 관리자 토큰을 돌려준다."""
        cache = type(self)._token_cache
        if cache.get('token') is None:
            status, data = self.req('POST', '/api/notices/auth', {'password': _ADMIN_PW})
            self.assertEqual(status, 200, f'관리자 로그인 실패: {data}')
            cache['token'] = data['data']['token']
        return cache['token']

    def assert_ok_envelope(self, data):
        self.assertIsNotNone(data)
        self.assertTrue(data.get('ok'), f'ok=true 여야 하는데: {data}')
        self.assertIsNone(data.get('error'))

    def assert_error_envelope(self, data):
        self.assertIsNotNone(data)
        self.assertFalse(data.get('ok'), f'ok=false 여야 하는데: {data}')
        self.assertIsNone(data.get('data'))
        self.assertIsNotNone(data.get('error'))

    def create_notice(self, title='테스트 공지', body='테스트 본문'):
        status, data = self.req('POST', '/api/notices', {'title': title, 'body': body},
                                 token=self.admin_token())
        self.assertIn(status, (200, 201), f'공지 생성 실패: {status} {data}')
        notice_id = data['data']['id']
        self.assertIsInstance(notice_id, int)
        self.addCleanup(self.req, 'DELETE', f'/api/notices/{notice_id}', None, self.admin_token())
        return notice_id

    def create_comment(self, notice_id, nickname='tester', password='pass1234', body='댓글 내용'):
        status, data = self.req('POST', f'/api/notices/{notice_id}/comments',
                                 {'nickname': nickname, 'password': password, 'body': body})
        self.assertIn(status, (200, 201), f'댓글 생성 실패: {status} {data}')
        comment_id = data['data']['id']
        self.assertIsInstance(comment_id, int)
        return comment_id


class NoticeCrudTests(_BaseAPITestCase):
    """공지 CRUD: 인증, 생성/조회/수정/삭제, 존재하지 않는 리소스 처리."""

    def test_create_without_token_401(self):
        status, data = self.req('POST', '/api/notices', {'title': 't', 'body': 'b'})
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_create_with_forged_token_401(self):
        status, data = self.req('POST', '/api/notices', {'title': 't', 'body': 'b'}, token='abc.def')
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_create_with_empty_token_401(self):
        status, data = self.req('POST', '/api/notices', {'title': 't', 'body': 'b'}, token='')
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_update_without_token_401(self):
        # Arrange
        notice_id = self.create_notice()
        # Act
        status, data = self.req('PUT', f'/api/notices/{notice_id}', {'title': 't2', 'body': 'b2'})
        # Assert
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_delete_without_token_401(self):
        # Arrange
        notice_id = self.create_notice()
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}')
        # Assert
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_create_success_returns_int_id(self):
        status, data = self.req('POST', '/api/notices', {'title': '생성 확인', 'body': '본문'},
                                 token=self.admin_token())
        self.assertIn(status, (200, 201))
        self.assert_ok_envelope(data)
        self.assertIsInstance(data['data']['id'], int)
        self.addCleanup(self.req, 'DELETE', f"/api/notices/{data['data']['id']}", None,
                         self.admin_token())

    def test_get_detail_matches_created(self):
        # Arrange
        notice_id = self.create_notice(title='상세 제목', body='상세 본문')
        # Act
        status, data = self.req('GET', f'/api/notices/{notice_id}')
        # Assert
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        detail = data['data']
        self.assertEqual(detail['title'], '상세 제목')
        self.assertEqual(detail['body'], '상세 본문')
        self.assertEqual(detail['id'], notice_id)
        self.assertEqual(detail['created_at'], detail['updated_at'])

    def test_update_reflected_and_updated_at_changes(self):
        # Arrange
        notice_id = self.create_notice(title='원본 제목', body='원본 본문')
        _, before = self.req('GET', f'/api/notices/{notice_id}')
        # Act
        status, data = self.req('PUT', f'/api/notices/{notice_id}',
                                 {'title': '수정된 제목', 'body': '수정된 본문'},
                                 token=self.admin_token())
        # Assert
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        _, after = self.req('GET', f'/api/notices/{notice_id}')
        self.assertEqual(after['data']['title'], '수정된 제목')
        self.assertEqual(after['data']['body'], '수정된 본문')
        self.assertNotEqual(after['data']['updated_at'], before['data']['created_at'])
        self.assertEqual(after['data']['created_at'], before['data']['created_at'])

    def test_delete_then_get_404(self):
        # Arrange
        notice_id = self.create_notice()
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}', token=self.admin_token())
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        # Assert
        status2, data2 = self.req('GET', f'/api/notices/{notice_id}')
        self.assertEqual(status2, 404)
        self.assert_error_envelope(data2)

    def test_get_nonexistent_id_404(self):
        status, data = self.req('GET', '/api/notices/999999999')
        self.assertEqual(status, 404)
        self.assert_error_envelope(data)

    def test_put_nonexistent_id_404(self):
        status, data = self.req('PUT', '/api/notices/999999999', {'title': 't', 'body': 'b'},
                                 token=self.admin_token())
        self.assertEqual(status, 404)
        self.assert_error_envelope(data)

    def test_delete_nonexistent_id_404(self):
        status, data = self.req('DELETE', '/api/notices/999999999', token=self.admin_token())
        self.assertEqual(status, 404)
        self.assert_error_envelope(data)

    def test_non_integer_id_returns_404(self):
        status_get, _ = self.req('GET', '/api/notices/abc')
        self.assertEqual(status_get, 404)
        status_put, _ = self.req('PUT', '/api/notices/abc', {'title': 't', 'body': 'b'},
                                  token=self.admin_token())
        self.assertEqual(status_put, 404)
        status_delete, _ = self.req('DELETE', '/api/notices/abc', token=self.admin_token())
        self.assertEqual(status_delete, 404)


class NoticeListTests(_BaseAPITestCase):
    """공지 목록: 정렬, 페이지네이션, 댓글 수 집계."""

    def test_list_ordered_by_id_desc(self):
        # Arrange
        id1 = self.create_notice(title='정렬 A')
        id2 = self.create_notice(title='정렬 B')
        id3 = self.create_notice(title='정렬 C')
        # Act
        status, data = self.req('GET', '/api/notices?page=1&limit=20')
        # Assert
        self.assertEqual(status, 200)
        items = data['data']['items']
        self.assertEqual([it['id'] for it in items], [id3, id2, id1])
        self.assertEqual(data['data']['total'], 3)

    def test_limit_restricts_items_but_total_is_full_count(self):
        # Arrange
        for i in range(5):
            self.create_notice(title=f'페이지 {i}')
        # Act
        status, data = self.req('GET', '/api/notices?page=1&limit=2')
        # Assert
        self.assertEqual(status, 200)
        self.assertEqual(len(data['data']['items']), 2)
        self.assertEqual(data['data']['total'], 5)

    def test_comment_count_matches_actual_comments(self):
        # Arrange
        notice_id = self.create_notice(title='댓글수 확인')
        self.create_comment(notice_id)
        self.create_comment(notice_id)
        # Act
        status, data = self.req('GET', '/api/notices?page=1&limit=20')
        # Assert
        self.assertEqual(status, 200)
        item = next(it for it in data['data']['items'] if it['id'] == notice_id)
        self.assertEqual(item['comment_count'], 2)

    def test_page_2_returns_correct_slice(self):
        # Arrange: 오래된 것부터 A, B, C 순으로 생성 -> 최신순 정렬이면 C, B, A
        # A, C 의 id 는 쓰지 않는다 - 2페이지에 걸리는 건 가운데 B 하나뿐이다.
        self.create_notice(title='페이지네이션 A')
        id_b = self.create_notice(title='페이지네이션 B')
        self.create_notice(title='페이지네이션 C')
        # Act
        status, data = self.req('GET', '/api/notices?page=2&limit=1')
        # Assert
        self.assertEqual(status, 200)
        self.assertEqual(len(data['data']['items']), 1)
        self.assertEqual(data['data']['items'][0]['id'], id_b)
        self.assertEqual(data['data']['total'], 3)

    def test_limit_over_max_is_clamped(self):
        status, data = self.req('GET', '/api/notices?page=1&limit=999')
        self.assertEqual(status, 200)
        self.assertLessEqual(data['data']['limit'], 50)

    def test_invalid_page_values_fall_back_to_default(self):
        for bad_page in ('0', '-1', 'abc'):
            with self.subTest(page=bad_page):
                status, data = self.req('GET', f'/api/notices?page={bad_page}&limit=10')
                self.assertNotEqual(status, 500, f'page={bad_page} 에서 500 이 나면 안 된다')
                self.assertEqual(status, 200, f'page={bad_page} 는 기본값으로 처리되어야 한다: {data}')


class NoticeAuthTests(_BaseAPITestCase):
    """관리자 로그인: 정상/오류 케이스만 (레이트 리밋은 별도 클래스)."""

    def test_correct_password_returns_token(self):
        status, data = self.req('POST', '/api/notices/auth', {'password': _ADMIN_PW})
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        self.assertIsInstance(data['data']['token'], str)
        self.assertTrue(data['data']['token'])
        self.assertIn('expires_at', data['data'])

    def test_wrong_password_401(self):
        status, data = self.req('POST', '/api/notices/auth', {'password': 'wrong-password-1'})
        self.assertEqual(status, 401)
        self.assert_error_envelope(data)

    def test_non_ascii_wrong_password_does_not_crash(self):
        """비 ASCII 비밀번호(한글 등)도 그냥 '틀린 비밀번호'로 401 이어야 한다.

        notice_auth.check_admin_password 가 hmac.compare_digest(password, ADMIN_PASSWORD)
        를 쓰는데, hmac.compare_digest 는 두 인자가 str 이면 둘 다 ASCII 여야 하고
        아니면 TypeError 를 던진다. 그 예외가 그대로 새면 500 이 되어버린다.
        """
        status, data = self.req('POST', '/api/notices/auth', {'password': '완전히-틀린-비번'})
        self.assertEqual(status, 401, f'비 ASCII 비밀번호 입력이 500 으로 죽으면 안 된다: {data}')
        self.assert_error_envelope(data)


class NoticeAuthRateLimitTests(_BaseAPITestCase):
    """auth 레이트 리밋(10회/600초)만 단독으로 검증하는 격리된 클래스.

    이 클래스는 이 파일의 유일한 테스트 메서드만 갖는다. setUp 에서 카운터를
    리셋한 직후 바로 11번 호출하기 때문에, 다른 테스트의 auth 호출과
    섞여서 카운트가 틀어질 걱정이 없다.
    """

    def test_11th_wrong_attempt_returns_429(self):
        statuses = []
        for _ in range(11):
            status, _ = self.req('POST', '/api/notices/auth', {'password': 'wrong-password'})
            statuses.append(status)
        self.assertEqual(statuses[:10], [401] * 10, f'처음 10회는 401 이어야 한다: {statuses}')
        self.assertEqual(statuses[10], 429, f'11번째 시도는 429 여야 한다: {statuses}')


class NoticeCommentTests(_BaseAPITestCase):
    """댓글: 작성/조회/삭제 권한, 캐스케이드 삭제."""

    def test_create_and_list_oldest_first_no_password_leak(self):
        # Arrange
        notice_id = self.create_notice(title='댓글 순서 확인')
        c1 = self.create_comment(notice_id, nickname='먼저')
        c2 = self.create_comment(notice_id, nickname='나중')
        # Act
        status, data = self.req('GET', f'/api/notices/{notice_id}/comments')
        # Assert
        self.assertEqual(status, 200)
        items = data['data']['items']
        self.assertEqual([it['id'] for it in items], [c1, c2])
        self.assertEqual(items[0]['nickname'], '먼저')
        raw_text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn('password_hash', raw_text)
        self.assertNotIn('pbkdf2', raw_text)

    def test_self_delete_with_correct_password_succeeds(self):
        # Arrange
        notice_id = self.create_notice()
        comment_id = self.create_comment(notice_id, password='mypw1234')
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}/comments/{comment_id}',
                                 {'password': 'mypw1234'})
        # Assert
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        _, listing = self.req('GET', f'/api/notices/{notice_id}/comments')
        self.assertNotIn(comment_id, [it['id'] for it in listing['data']['items']])

    def test_delete_with_wrong_password_403_and_survives(self):
        # Arrange
        notice_id = self.create_notice()
        comment_id = self.create_comment(notice_id, password='rightpw1')
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}/comments/{comment_id}',
                                 {'password': 'wrongpw9'})
        # Assert
        self.assertEqual(status, 403)
        self.assert_error_envelope(data)
        _, listing = self.req('GET', f'/api/notices/{notice_id}/comments')
        self.assertIn(comment_id, [it['id'] for it in listing['data']['items']])

    def test_admin_delete_without_password_succeeds(self):
        # Arrange
        notice_id = self.create_notice()
        comment_id = self.create_comment(notice_id)
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}/comments/{comment_id}',
                                 token=self.admin_token())
        # Assert
        self.assertEqual(status, 200)
        self.assert_ok_envelope(data)
        _, listing = self.req('GET', f'/api/notices/{notice_id}/comments')
        self.assertNotIn(comment_id, [it['id'] for it in listing['data']['items']])

    def test_comment_on_nonexistent_notice_404(self):
        status, data = self.req('POST', '/api/notices/999999999/comments',
                                 {'nickname': 'x', 'password': 'pass1234', 'body': 'b'})
        self.assertEqual(status, 404)
        self.assert_error_envelope(data)

    def test_delete_comment_via_wrong_notice_id_404(self):
        # Arrange
        notice1 = self.create_notice(title='진짜 부모')
        notice2 = self.create_notice(title='엉뚱한 공지')
        comment_id = self.create_comment(notice1, password='pw123456')
        # Act: notice2 경로로 notice1 소속 댓글을 지우려 한다
        status, data = self.req('DELETE', f'/api/notices/{notice2}/comments/{comment_id}',
                                 {'password': 'pw123456'})
        # Assert
        self.assertEqual(status, 404)
        self.assert_error_envelope(data)
        _, listing = self.req('GET', f'/api/notices/{notice1}/comments')
        self.assertIn(comment_id, [it['id'] for it in listing['data']['items']])

    def test_cascade_delete_removes_comments(self):
        # Arrange
        notice_id = self.create_notice(title='캐스케이드 확인')
        self.create_comment(notice_id)
        self.create_comment(notice_id)
        # Act
        status, data = self.req('DELETE', f'/api/notices/{notice_id}', token=self.admin_token())
        self.assertEqual(status, 200)
        # Assert: API 로도 404, DB 에도 댓글 행이 안 남는다
        status2, _ = self.req('GET', f'/api/notices/{notice_id}/comments')
        self.assertEqual(status2, 404)
        with notice_db._connect() as conn:
            remaining = conn.execute(
                'SELECT COUNT(*) FROM comments WHERE notice_id = ?', (notice_id,)
            ).fetchone()[0]
        self.assertEqual(remaining, 0, 'ON DELETE CASCADE 로 댓글이 함께 삭제되어야 한다')


class NoticeValidationTests(_BaseAPITestCase):
    """입력 검증: 전부 400 을 기대한다."""

    def setUp(self):
        super().setUp()
        # 댓글 검증 테스트들이 공유할 유효한 공지 하나. 검증 실패 케이스만
        # 다루므로 실제로 댓글이 쌓이진 않지만, 혹시 몰라 매 테스트 전에
        # 새로 만들고 addCleanup 으로 지운다 (다른 테스트와 상태를 공유하지 않는다).
        self.notice_id = self.create_notice(title='검증용 공지')

    def test_title_too_long_400(self):
        status, data = self.req('POST', '/api/notices',
                                 {'title': 'x' * (TITLE_MAX + 1), 'body': 'b'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_title_empty_400(self):
        status, data = self.req('POST', '/api/notices', {'title': '', 'body': 'b'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_title_whitespace_only_400(self):
        status, data = self.req('POST', '/api/notices', {'title': '    ', 'body': 'b'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_body_too_long_400(self):
        status, data = self.req('POST', '/api/notices',
                                 {'title': 't', 'body': 'x' * (BODY_MAX + 1)},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_body_empty_400(self):
        status, data = self.req('POST', '/api/notices', {'title': 't', 'body': ''},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_nickname_too_long_400(self):
        status, data = self.req('POST', f'/api/notices/{self.notice_id}/comments',
                                 {'nickname': 'n' * (NICK_MAX + 1), 'password': 'pass1234',
                                  'body': 'b'})
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_nickname_empty_400(self):
        status, data = self.req('POST', f'/api/notices/{self.notice_id}/comments',
                                 {'nickname': '', 'password': 'pass1234', 'body': 'b'})
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_comment_body_too_long_400(self):
        status, data = self.req('POST', f'/api/notices/{self.notice_id}/comments',
                                 {'nickname': 'n', 'password': 'pass1234',
                                  'body': 'x' * (COMMENT_BODY_MAX + 1)})
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_comment_password_too_short_400(self):
        status, data = self.req('POST', f'/api/notices/{self.notice_id}/comments',
                                 {'nickname': 'n', 'password': 'x' * (PASSWORD_MIN - 1),
                                  'body': 'b'})
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_title_not_a_string_400(self):
        for bad_title in (123, None, ['a']):
            with self.subTest(title=bad_title):
                status, data = self.req('POST', '/api/notices', {'title': bad_title, 'body': 'b'},
                                         token=self.admin_token())
                self.assertEqual(status, 400, f'title={bad_title!r} 는 400 이어야 한다: {data}')
                self.assert_error_envelope(data)

    def test_missing_field_notice_400(self):
        status, data = self.req('POST', '/api/notices', {'title': '제목만 있음'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_missing_field_comment_400(self):
        base = {'nickname': 'n', 'password': 'pass1234', 'body': 'b'}
        for missing in ('nickname', 'password', 'body'):
            with self.subTest(missing=missing):
                payload = {k: v for k, v in base.items() if k != missing}
                status, data = self.req('POST', f'/api/notices/{self.notice_id}/comments', payload)
                self.assertEqual(status, 400, f'{missing} 누락은 400 이어야 한다: {data}')
                self.assert_error_envelope(data)


class NoticeRequestFormatTests(_BaseAPITestCase):
    """요청 형식: Content-Type, JSON 파싱, 본문 크기 제한."""

    def test_post_without_content_type_415(self):
        payload = json.dumps({'title': 't', 'body': 'b'}).encode('utf-8')
        status, data = self.req('POST', '/api/notices', raw_body=payload, headers={},
                                 token=self.admin_token())
        self.assertEqual(status, 415)
        self.assert_error_envelope(data)

    def test_post_wrong_content_type_415(self):
        payload = json.dumps({'title': 't', 'body': 'b'}).encode('utf-8')
        status, data = self.req('POST', '/api/notices', raw_body=payload,
                                 headers={'Content-Type': 'text/plain'}, token=self.admin_token())
        self.assertEqual(status, 415)
        self.assert_error_envelope(data)

    def test_broken_json_400(self):
        status, data = self.req('POST', '/api/notices', raw_body=b'{not valid json',
                                 headers={'Content-Type': 'application/json'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_top_level_array_400(self):
        status, data = self.req('POST', '/api/notices', raw_body=b'[1, 2, 3]',
                                 headers={'Content-Type': 'application/json'},
                                 token=self.admin_token())
        self.assertEqual(status, 400)
        self.assert_error_envelope(data)

    def test_content_length_over_65536_returns_413(self):
        big_payload = json.dumps({'title': '큰 요청', 'body': 'a' * 70000}).encode('utf-8')
        self.assertGreater(len(big_payload), 65536)
        status, data = self.req('POST', '/api/notices', raw_body=big_payload,
                                 headers={'Content-Type': 'application/json'},
                                 token=self.admin_token())
        self.assertEqual(status, 413)
        self.assert_error_envelope(data)


class NoticeSecurityTests(_BaseAPITestCase):
    """SQL 인젝션 방어, XSS 이스케이프 안 함(클라이언트 책임), 에러 정보 비노출."""

    def test_sql_injection_in_title_is_stored_verbatim_and_table_survives(self):
        # Arrange / Act
        payload_title = "'; DROP TABLE notices;--"
        notice_id = self.create_notice(title=payload_title, body='본문')
        # Assert: 테이블이 살아있어야 목록 조회가 정상 동작한다
        status, listing = self.req('GET', '/api/notices?page=1&limit=20')
        self.assertEqual(status, 200)
        status2, detail = self.req('GET', f'/api/notices/{notice_id}')
        self.assertEqual(status2, 200)
        self.assertEqual(detail['data']['title'], payload_title)

    def test_html_in_title_not_escaped(self):
        payload = '<img src=x onerror=alert(1)>'
        notice_id = self.create_notice(title=payload, body='본문')
        status, data = self.req('GET', f'/api/notices/{notice_id}')
        self.assertEqual(status, 200)
        self.assertEqual(data['data']['title'], payload, '서버가 이스케이프하면 안 된다(클라이언트 책임)')

    def test_html_in_comment_body_not_escaped(self):
        notice_id = self.create_notice()
        payload = '<img src=x onerror=alert(1)>'
        comment_id = self.create_comment(notice_id, body=payload)
        status, data = self.req('GET', f'/api/notices/{notice_id}/comments')
        self.assertEqual(status, 200)
        item = next(it for it in data['data']['items'] if it['id'] == comment_id)
        self.assertEqual(item['body'], payload, '서버가 이스케이프하면 안 된다(클라이언트 책임)')

    def test_error_response_never_leaks_internal_details(self):
        """id 자리에 SQLite INTEGER 범위(8바이트)를 넘는 큰 수를 넣어 DB 계층에서
        예외(OverflowError)가 나도록 유도한다. 500 이 뜨든 다른 방식으로
        방어되든, 에러 메시지에 파일 경로나 트레이스백이 새면 안 된다.
        """
        huge_id = '9' * 40
        status, data = self.req('GET', f'/api/notices/{huge_id}')
        self.assertIsNotNone(data, f'응답이 JSON 이 아니다: status={status}')
        self.assertGreaterEqual(status, 400)
        self.assertFalse(data.get('ok'))
        error_text = str(data.get('error') or '')
        leak_markers = ['Traceback', 'File "', '.py', '/Users/', 'site-packages',
                         _TMP_DB_PATH, 'line ', 'OverflowError']
        for marker in leak_markers:
            self.assertNotIn(marker, error_text,
                              f'에러 메시지에 내부 정보가 샜다: {error_text!r}')


class FaviconTests(_BaseAPITestCase):
    """파비콘: 루트 /favicon.ico 라우팅과 HTML 이 거는 아이콘 파일의 실재 여부."""

    # SimpleHTTPRequestHandler 는 요청 시점의 CWD 를 문서 루트로 삼는다. 테스트가
    # 어느 디렉토리에서 돌든 저장소 루트를 보도록 이 파일 위치를 기준으로 잡는다.
    REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
    ICO_MAGIC = b'\x00\x00\x01\x00'

    def setUp(self):
        super().setUp()
        cwd = os.getcwd()
        os.chdir(self.REPO_ROOT)
        self.addCleanup(os.chdir, cwd)

    def raw_get(self, path, method='GET'):
        """정적 파일용. req() 는 JSON 을 기대하므로 바이너리는 여기서 직접 받는다."""
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path)
            res = conn.getresponse()
            return res.status, res.getheader('Content-Type'), res.read()
        finally:
            conn.close()

    def test_root_favicon_ico_serves_icon(self):
        status, ctype, body = self.raw_get('/favicon.ico')
        self.assertEqual(status, 200, '루트 /favicon.ico 가 404 면 즐겨찾기·크롤러가 아이콘을 못 찾는다')
        self.assertTrue(body.startswith(self.ICO_MAGIC), f'ICO 헤더가 아니다: {body[:8]!r}')
        self.assertIsNotNone(ctype)

    def test_root_favicon_ico_head_matches_get(self):
        # do_GET 만 고치고 do_HEAD 를 빠뜨리는 실수를 잡는다.
        status, _, body = self.raw_get('/favicon.ico', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')

    def test_favicon_query_string_still_routes(self):
        # 캐시 무효화용 ?v= 를 붙여도 경로 판정이 깨지면 안 된다.
        status, _, body = self.raw_get('/favicon.ico?v=2')
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(self.ICO_MAGIC))

    def test_favicon_prefix_paths_are_not_hijacked(self):
        # '/favicon.icon' 같은 경로까지 잘못 붙잡으면 안 된다 (정적 파일 없음 -> 404).
        status, _, _ = self.raw_get('/favicon.icon')
        self.assertEqual(status, 404)

    def test_index_icon_links_all_resolve(self):
        """index.html 이 거는 아이콘 경로를 실제로 GET 해서 200 을 확인한다.

        파일명을 바꾸고 링크를 안 고치면 탭 아이콘이 조용히 빈칸이 된다. CI 도
        같은 검사를 하지만, 로컬에서 먼저 걸리는 편이 낫다.
        """
        with open(os.path.join(self.REPO_ROOT, 'index.html'), encoding='utf-8') as f:
            html = f.read()
        hrefs = re.findall(r'<link rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"', html)
        self.assertGreaterEqual(len(hrefs), 2, f'아이콘 link 태그를 못 찾았다: {hrefs}')
        for href in hrefs:
            with self.subTest(href=href):
                status, _, body = self.raw_get(href)
                self.assertEqual(status, 200, f'{href} 가 200 이 아니다')
                self.assertGreater(len(body), 0, f'{href} 가 빈 파일이다')

    def test_all_pages_share_the_same_icon_block(self):
        """네 페이지가 같은 아이콘 세트를 걸어야 탭을 옮겨다녀도 아이콘이 안 바뀐다."""
        pages = ('index.html', 'notice.html', 'notice-detail.html', 'notice-form.html')
        sets = {}
        for name in pages:
            with open(os.path.join(self.REPO_ROOT, name), encoding='utf-8') as f:
                html = f.read()
            sets[name] = frozenset(
                re.findall(r'<link rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"', html)
            )
        self.assertEqual(len(set(sets.values())), 1, f'페이지마다 아이콘이 다르다: {sets}')


if __name__ == '__main__':
    unittest.main()

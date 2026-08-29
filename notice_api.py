"""공지사항 REST API: 표준 라이브러리 http.server 핸들러 위에서 동작한다.

모든 응답은 { ok, data, error } 봉투로 통일한다. 예외가 클라이언트로 새면
내부 경로/스택이 노출될 수 있으므로 handle() 전체를 감싸서 500 + 고정
메시지로만 응답하고, 실제 traceback 은 서버 stderr 에만 남긴다.
"""
import json
import traceback
import urllib.parse

import notice_auth
import notice_db

MAX_BODY_BYTES = 65536


def handle(handler, method: str) -> None:
    """진입점. 응답 전송까지 여기서 끝낸다."""
    try:
        _dispatch(handler, method)
    except Exception:
        traceback.print_exc()
        _send_json(handler, 500, error="서버 오류가 발생했습니다")


def _dispatch(handler, method: str) -> None:
    parsed = urllib.parse.urlsplit(handler.path)
    query = urllib.parse.parse_qs(parsed.query)
    parts = [p for p in parsed.path.split('/') if p != '']

    if len(parts) < 2 or parts[0] != 'api' or parts[1] != 'notices':
        _send_json(handler, 404, error="요청하신 경로를 찾을 수 없습니다")
        return

    rest = parts[2:]

    # '/api/notices/auth' 는 '/api/notices/{id}' 보다 먼저 검사한다.
    # 안 그러면 'auth' 를 id 로 파싱하려다 실패해서 엉뚱하게 404 가 난다.
    if len(rest) == 1 and rest[0] == 'auth':
        if method != 'POST':
            _send_json(handler, 405, error="허용되지 않은 메서드입니다")
            return
        _handle_auth(handler)
        return

    if len(rest) == 0:
        if method == 'GET':
            _handle_list_notices(handler, query)
        elif method == 'POST':
            _handle_create_notice(handler)
        else:
            _send_json(handler, 405, error="허용되지 않은 메서드입니다")
        return

    notice_id = _parse_int(rest[0])
    if notice_id is None:
        _send_json(handler, 404, error="요청하신 경로를 찾을 수 없습니다")
        return

    if len(rest) == 1:
        if method == 'GET':
            _handle_get_notice(handler, notice_id)
        elif method == 'PUT':
            _handle_update_notice(handler, notice_id)
        elif method == 'DELETE':
            _handle_delete_notice(handler, notice_id)
        else:
            _send_json(handler, 405, error="허용되지 않은 메서드입니다")
        return

    if len(rest) == 2 and rest[1] == 'comments':
        if method == 'GET':
            _handle_list_comments(handler, notice_id)
        elif method == 'POST':
            _handle_create_comment(handler, notice_id)
        else:
            _send_json(handler, 405, error="허용되지 않은 메서드입니다")
        return

    if len(rest) == 3 and rest[1] == 'comments':
        comment_id = _parse_int(rest[2])
        if comment_id is None:
            _send_json(handler, 404, error="요청하신 경로를 찾을 수 없습니다")
            return
        if method == 'DELETE':
            _handle_delete_comment(handler, notice_id, comment_id)
        else:
            _send_json(handler, 405, error="허용되지 않은 메서드입니다")
        return

    _send_json(handler, 404, error="요청하신 경로를 찾을 수 없습니다")


# ---- 라우트 핸들러 -----------------------------------------------------

def _handle_auth(handler) -> None:
    if not notice_auth.rate_limit(notice_auth.client_ip(handler), 'notice_auth', 10, 600):
        _send_json(handler, 429, error="요청이 너무 많습니다. 잠시 후 다시 시도해주세요")
        return

    body, sent = _read_body(handler)
    if sent:
        return

    password, err = _validate_password(body, 'password')
    if err:
        _send_json(handler, 400, error=err)
        return

    if not notice_auth.check_admin_password(password):
        _send_json(handler, 401, error="비밀번호가 올바르지 않습니다")
        return

    token, expires_at = notice_auth.issue_admin_token()
    _send_json(handler, 200, data={'token': token, 'expires_at': expires_at})


def _handle_list_notices(handler, query) -> None:
    page = _parse_page(query)
    limit = _parse_limit(query)
    items, total = notice_db.list_notices(page, limit)
    _send_json(handler, 200, data={'items': items, 'total': total, 'page': page, 'limit': limit})


def _handle_get_notice(handler, notice_id: int) -> None:
    notice = notice_db.get_notice(notice_id)
    if notice is None:
        _send_json(handler, 404, error="공지사항을 찾을 수 없습니다")
        return
    _send_json(handler, 200, data=notice)


def _handle_create_notice(handler) -> None:
    body, sent = _read_body(handler)
    if sent:
        return

    if not _require_admin(handler):
        return

    title, err = _validate_title(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    notice_body, err = _validate_notice_body(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    new_id = notice_db.create_notice(title, notice_body)
    _send_json(handler, 200, data={'id': new_id})


def _handle_update_notice(handler, notice_id: int) -> None:
    body, sent = _read_body(handler)
    if sent:
        return

    if not _require_admin(handler):
        return

    title, err = _validate_title(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    notice_body, err = _validate_notice_body(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    updated = notice_db.update_notice(notice_id, title, notice_body)
    if not updated:
        _send_json(handler, 404, error="공지사항을 찾을 수 없습니다")
        return
    _send_json(handler, 200, data={'ok': True})


def _handle_delete_notice(handler, notice_id: int) -> None:
    body, sent = _read_body(handler)
    if sent:
        return

    if not _require_admin(handler):
        return

    deleted = notice_db.delete_notice(notice_id)
    if not deleted:
        _send_json(handler, 404, error="공지사항을 찾을 수 없습니다")
        return
    _send_json(handler, 200, data={'ok': True})


def _handle_list_comments(handler, notice_id: int) -> None:
    notice = notice_db.get_notice(notice_id)
    if notice is None:
        _send_json(handler, 404, error="공지사항을 찾을 수 없습니다")
        return
    items = notice_db.list_comments(notice_id)
    _send_json(handler, 200, data={'items': items})


def _handle_create_comment(handler, notice_id: int) -> None:
    if not notice_auth.rate_limit(notice_auth.client_ip(handler), 'notice_comment_create', 5, 600):
        _send_json(handler, 429, error="요청이 너무 많습니다. 잠시 후 다시 시도해주세요")
        return

    body, sent = _read_body(handler)
    if sent:
        return

    notice = notice_db.get_notice(notice_id)
    if notice is None:
        _send_json(handler, 404, error="공지사항을 찾을 수 없습니다")
        return

    nickname, err = _validate_nickname(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    comment_body, err = _validate_comment_body(body)
    if err:
        _send_json(handler, 400, error=err)
        return

    password, err = _validate_password(body, 'password')
    if err:
        _send_json(handler, 400, error=err)
        return

    password_hash = notice_auth.hash_password(password)
    new_id = notice_db.create_comment(notice_id, nickname, password_hash, comment_body)
    _send_json(handler, 200, data={'id': new_id})


def _handle_delete_comment(handler, notice_id: int, comment_id: int) -> None:
    if not notice_auth.rate_limit(notice_auth.client_ip(handler), 'notice_comment_delete', 20, 600):
        _send_json(handler, 429, error="요청이 너무 많습니다. 잠시 후 다시 시도해주세요")
        return

    body, sent = _read_body(handler)
    if sent:
        return

    comment = notice_db.get_comment(comment_id)
    if comment is None or comment['notice_id'] != notice_id:
        _send_json(handler, 404, error="댓글을 찾을 수 없습니다")
        return

    # 관리자 토큰이 유효하면 비밀번호 검증 없이 통과시킨다.
    token = _extract_bearer_token(handler)
    if notice_auth.verify_admin_token(token):
        notice_db.delete_comment(comment_id)
        _send_json(handler, 200, data={'ok': True})
        return

    password, err = _validate_password(body, 'password')
    if err:
        _send_json(handler, 400, error=err)
        return

    if not notice_auth.verify_password(password, comment['password_hash']):
        _send_json(handler, 403, error="비밀번호가 일치하지 않습니다")
        return

    notice_db.delete_comment(comment_id)
    _send_json(handler, 200, data={'ok': True})


# ---- 인증 -----------------------------------------------------------

def _extract_bearer_token(handler) -> str | None:
    header = handler.headers.get('Authorization')
    if not header:
        return None
    scheme, _, token = header.partition(' ')
    if scheme != 'Bearer' or not token:
        return None
    return token.strip()


def _require_admin(handler) -> bool:
    token = _extract_bearer_token(handler)
    if not notice_auth.verify_admin_token(token):
        _send_json(handler, 401, error="관리자 인증이 필요합니다")
        return False
    return True


# ---- 요청 본문 읽기 ----------------------------------------------------

def _get_content_length(handler) -> int:
    raw = handler.headers.get('Content-Length')
    if raw is None:
        return 0
    try:
        length = int(raw)
    except ValueError:
        return 0
    return length if length > 0 else 0


def _read_body(handler) -> tuple[dict | None, bool]:
    """요청 본문을 읽어 dict 로 반환한다. 실패 시 에러 응답을 직접 보내고 (None, True) 를 반환한다.

    순서: Content-Length 확인(65536 초과면 rfile 을 읽지 않고 즉시 413) ->
    본문이 없으면(Content-Length 0/없음) 빈 dict 로 취급하고 Content-Type 검사를 생략 ->
    Content-Type 이 application/json 이 아니면 415(폼 기반 요청으로 오는 CSRF 를 막기 위함) ->
    JSON 파싱 실패/최상위가 dict 아니면 400.
    """
    length = _get_content_length(handler)
    if length > MAX_BODY_BYTES:
        _send_json(handler, 413, error="요청 본문이 너무 큽니다")
        return None, True

    if length == 0:
        return {}, False

    raw = handler.rfile.read(length)

    content_type = handler.headers.get('Content-Type', '')
    base_type = content_type.split(';')[0].strip().lower()
    if base_type != 'application/json':
        _send_json(handler, 415, error="Content-Type 은 application/json 이어야 합니다")
        return None, True

    try:
        parsed = json.loads(raw.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _send_json(handler, 400, error="요청 본문이 올바른 JSON 형식이 아닙니다")
        return None, True

    if not isinstance(parsed, dict):
        _send_json(handler, 400, error="요청 본문은 객체 형식이어야 합니다")
        return None, True

    return parsed, False


# ---- 입력 검증 --------------------------------------------------------

def _validate_title(body: dict) -> tuple[str | None, str | None]:
    val = body.get('title')
    if not isinstance(val, str):
        return None, "제목을 입력해주세요"
    title = val.strip()
    if not (1 <= len(title) <= 200):
        return None, "제목은 1자 이상 200자 이하여야 합니다"
    return title, None


def _validate_notice_body(body: dict) -> tuple[str | None, str | None]:
    val = body.get('body')
    if not isinstance(val, str):
        return None, "내용을 입력해주세요"
    text = val.strip()
    if not (1 <= len(text) <= 10000):
        return None, "내용은 1자 이상 10,000자 이하여야 합니다"
    return text, None


def _validate_nickname(body: dict) -> tuple[str | None, str | None]:
    val = body.get('nickname')
    if not isinstance(val, str):
        return None, "닉네임을 입력해주세요"
    nickname = val.strip()
    if not (1 <= len(nickname) <= 20):
        return None, "닉네임은 1자 이상 20자 이하여야 합니다"
    return nickname, None


def _validate_comment_body(body: dict) -> tuple[str | None, str | None]:
    val = body.get('body')
    if not isinstance(val, str):
        return None, "댓글 내용을 입력해주세요"
    text = val.strip()
    if not (1 <= len(text) <= 1000):
        return None, "댓글 내용은 1자 이상 1,000자 이하여야 합니다"
    return text, None


def _validate_password(body: dict, key: str) -> tuple[str | None, str | None]:
    """비밀번호는 strip 하지 않는다 - 앞뒤 공백도 비밀번호의 일부로 취급한다."""
    val = body.get(key)
    if not isinstance(val, str):
        return None, "비밀번호를 입력해주세요"
    if not (4 <= len(val) <= 64):
        return None, "비밀번호는 4자 이상 64자 이하여야 합니다"
    return val, None


# sqlite 의 INTEGER 는 부호 있는 64비트다. 이 범위를 넘는 id 를 그대로 넘기면
# 쿼리 단계에서 OverflowError 가 나 500 으로 떨어진다. 존재할 수 없는 id 이므로
# 여기서 걸러 404 로 보낸다.
_SQLITE_INT_MAX = 2 ** 63 - 1
_SQLITE_INT_MIN = -(2 ** 63)


def _parse_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < _SQLITE_INT_MIN or parsed > _SQLITE_INT_MAX:
        return None
    return parsed


def _parse_page(query: dict) -> int:
    raw = query.get('page', ['1'])[0]
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _parse_limit(query: dict) -> int:
    raw = query.get('limit', ['20'])[0]
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return 20
    if limit < 1:
        return 1
    if limit > 50:
        return 50
    return limit


# ---- 응답 -------------------------------------------------------------

def _send_json(handler, status: int, data=None, error: str | None = None) -> None:
    payload = json.dumps(
        {'ok': error is None, 'data': data, 'error': error},
        ensure_ascii=False,
    ).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)

"""관리자 인증: 비밀번호 해시/검증, HMAC 서명 토큰 발급/검증, 레이트 리밋.

기본 비밀번호(ADMIN_PASSWORD='0000')는 4자리 숫자라 조합이 1만 개뿐이고,
그 자체로는 방어력이 거의 없다. rate_limit() 이 실질적인 방어선이며,
운영 환경에서는 반드시 ADMIN_PASSWORD 환경변수로 강한 값을 넣어야 한다.

SECRET_KEY 를 환경변수로 지정하지 않으면 프로세스가 뜰 때마다 랜덤 32바이트를
새로 뽑는다. 그러면 서버를 재시작할 때마다 이전에 발급된 토큰의 서명이 전부
무효가 되는데, 이건 버그가 아니라 의도된 동작이다 (재시작 후에는 다시 로그인).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '0000')
SECRET_KEY = os.environ.get('SECRET_KEY', '').encode() or secrets.token_bytes(32)
TOKEN_TTL = 2 * 3600

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 으로 비밀번호를 해시한다. salt 는 호출마다 새로 뽑는다."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
    return f'pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    """형식이 깨졌거나 알고리즘이 다르거나 hex 파싱이 실패해도 예외 대신 False.

    iteration 수는 저장된 값을 그대로 써서 검증한다. 나중에 PBKDF2_ITERATIONS 를
    올려도 예전에 발급된 해시가 계속 검증되게 하려는 것이다.
    """
    try:
        algorithm, iterations_str, salt_hex, hash_hex = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    except (ValueError, AttributeError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_admin_password(password: str) -> bool:
    """상수시간 비교로 타이밍 공격을 막는다.

    바이트로 바꿔서 비교하는 이유: compare_digest 는 str 을 받으면 양쪽이 모두
    ASCII 여야 하고 아니면 TypeError 를 던진다. 한글 비밀번호를 넣었을 때
    401 대신 500 이 나가던 문제가 여기서 나왔다. bytes 에는 그 제약이 없다.
    """
    if not isinstance(password, str):
        return False
    try:
        candidate = password.encode('utf-8')
    except UnicodeEncodeError:
        return False  # 서로게이트 등 인코딩 불가 입력은 비밀번호가 될 수 없다
    return hmac.compare_digest(candidate, ADMIN_PASSWORD.encode('utf-8'))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _b64url_decode(s: str) -> bytes:
    padding = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def issue_admin_token() -> tuple[str, str]:
    """HMAC 서명 토큰을 발급한다. (token, expires_at_iso) 를 반환한다."""
    exp = int(time.time()) + TOKEN_TTL
    payload_bytes = json.dumps({'exp': exp}).encode('utf-8')
    signature = hmac.new(SECRET_KEY, payload_bytes, hashlib.sha256).digest()
    token = f'{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}'
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
    return token, expires_at


def verify_admin_token(token: str | None) -> bool:
    """어떤 입력에도 예외를 던지지 않고 False 로 처리한다.

    서명 검증을 payload 파싱보다 먼저 한다 - 위조된 JSON 을 파싱하는 상황 자체를
    피하기 위해서다.
    """
    if not token or not isinstance(token, str) or token.count('.') != 1:
        return False

    payload_b64, sig_b64 = token.split('.')
    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
    except ValueError:
        return False

    expected_sig = hmac.new(SECRET_KEY, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_sig):
        return False

    try:
        payload = json.loads(payload_bytes.decode('utf-8'))
        exp = payload['exp']
    except (ValueError, KeyError, TypeError):
        return False
    if not isinstance(exp, int) or isinstance(exp, bool):
        return False

    return exp >= int(time.time())


# 메모리 기반 레이트 리밋 저장소. 프로세스 재시작 시 초기화되지만, 이 서버는
# 단일 프로세스로만 돌기 때문에 그걸로 충분하다. 테스트에서 리셋할 수 있도록
# 모듈 전역으로 노출한다.
_BUCKETS: dict[tuple[str, str], list[float]] = {}
_BUCKETS_LOCK = threading.Lock()


def rate_limit(key: str, bucket: str, limit: int, window_sec: int) -> bool:
    """슬라이딩 윈도우 레이트 리밋. True 면 통과(카운트 1 증가), False 면 한도 초과.

    ThreadingHTTPServer 위에서 여러 스레드가 동시에 호출하므로 락으로 보호하고,
    호출할 때마다 윈도우 밖 타임스탬프를 버려서 메모리가 무한히 늘지 않게 한다.
    """
    now = time.time()
    bucket_key = (key, bucket)
    with _BUCKETS_LOCK:
        timestamps = [t for t in _BUCKETS.get(bucket_key, []) if now - t < window_sec]
        if len(timestamps) >= limit:
            _BUCKETS[bucket_key] = timestamps
            return False
        timestamps.append(now)
        _BUCKETS[bucket_key] = timestamps
        return True


def reset_rate_limits() -> None:
    """테스트에서 레이트 리밋 카운터를 초기화하기 위한 헬퍼."""
    with _BUCKETS_LOCK:
        _BUCKETS.clear()


def client_ip(handler) -> str:
    """클라이언트 IP 를 얻는다. 기본은 handler.client_address[0].

    TRUST_PROXY=1 일 때만 X-Forwarded-For 헤더를 신뢰한다. 그 헤더는 클라이언트가
    마음대로 위조할 수 있어서, 기본값으로 그대로 믿으면 헤더만 바꿔가며 레이트
    리밋을 우회당한다. 실제로 리버스 프록시 뒤에 있는 배포 환경에서만 켜라.
    """
    if os.environ.get('TRUST_PROXY') == '1':
        forwarded = handler.headers.get('X-Forwarded-For')
        if forwarded:
            first = forwarded.split(',')[0].strip()
            if first:
                return first
    return handler.client_address[0]

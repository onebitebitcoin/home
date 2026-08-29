/* ============================================================
   공지사항 공통 유틸 (전역 함수, ES 모듈 아님)
   notice-list.js / notice-detail.js / notice-form.js 보다 먼저 로드된다.
   ============================================================ */

/**
 * HTML 특수문자를 엔티티로 이스케이프한다.
 * 공지 제목/본문, 댓글 닉네임/본문을 innerHTML 로 넣기 전에는 반드시 이 함수를 통과시킨다.
 * 서버는 원문을 그대로 저장/반환하므로 XSS 방어는 전적으로 클라이언트 책임이다.
 * @param {unknown} value
 * @returns {string}
 */
function esc(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const NOTICE_TOKEN_KEY = 'notice_admin_token';
const NOTICE_TOKEN_EXP_KEY = 'notice_admin_token_exp';

/**
 * sessionStorage 에서 관리자 토큰을 읽는다. 만료됐으면 지우고 null 을 반환한다.
 * @returns {string | null}
 */
function adminToken() {
  // 사파리 프라이빗 모드 등에서는 접근만으로도 예외가 난다. 여기서 새면
  // api() 가 매번 터져 페이지 전체가 멈춘다.
  let token, expiresAt;
  try {
    token = sessionStorage.getItem(NOTICE_TOKEN_KEY);
    expiresAt = sessionStorage.getItem(NOTICE_TOKEN_EXP_KEY);
  } catch (e) {
    return null;
  }
  if (!token || !expiresAt) return null;
  if (Number.isNaN(Date.parse(expiresAt)) || new Date(expiresAt).getTime() <= Date.now()) {
    clearAdminToken();
    return null;
  }
  return token;
}

/**
 * @param {string} token
 * @param {string} expiresAt UTC ISO8601 문자열
 * @returns {void}
 */
function setAdminToken(token, expiresAt) {
  try {
    sessionStorage.setItem(NOTICE_TOKEN_KEY, token);
    sessionStorage.setItem(NOTICE_TOKEN_EXP_KEY, expiresAt);
  } catch (e) {
    /* 저장이 막힌 환경. 이번 화면에서만 관리자 상태가 안 이어진다 */
  }
}

/** @returns {void} */
function clearAdminToken() {
  try {
    sessionStorage.removeItem(NOTICE_TOKEN_KEY);
    sessionStorage.removeItem(NOTICE_TOKEN_EXP_KEY);
  } catch (e) {
    /* 위와 같다 */
  }
}

/**
 * UTC ISO8601 문자열을 'YYYY.MM.DD HH:mm' (Asia/Seoul 기준) 형식으로 바꾼다.
 * @param {string} iso
 * @returns {string}
 */
function fmtDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const parts = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}.${get('month')}.${get('day')} ${get('hour')}:${get('minute')}`;
}

/* ── 관리자 진입 게이트 ────────────────────────────────────
   관리자 버튼을 아무에게나 보이지 않게 하는 장치. 권한이 아니라 노출만
   가른다 — 실제 방어선은 서버의 비밀번호와 레이트 리밋이다.

   /notice?admin=<문구> 로 한 번 들어오면 그 기기에 표식이 남고, 이후로는
   버튼이 그냥 보인다. 소스에는 문구의 SHA-256 만 두어 이 파일을 열어봐도
   문구가 드러나지 않는다.

   crypto.subtle 은 보안 컨텍스트(https 또는 localhost)에서만 있다.
   없으면 조용히 실패시키지 않고 사유를 알린다. */
const ADMIN_GATE_HASH = '2dd065ce494214a2be5ab41b5caa411d14108ce56a355afaf95d400d84816778';
const ADMIN_GATE_KEY = 'notice_admin_unlocked';

function adminUnlocked() {
  try {
    return localStorage.getItem(ADMIN_GATE_KEY) === '1';
  } catch (e) {
    return false;
  }
}

function setAdminUnlocked(on) {
  try {
    if (on) localStorage.setItem(ADMIN_GATE_KEY, '1');
    else localStorage.removeItem(ADMIN_GATE_KEY);
  } catch (e) {
    /* 사파리 프라이빗 모드 등. 이번 방문에만 안 남을 뿐이다 */
  }
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * 주소의 ?admin= 값을 확인해 표식을 켜거나 끈다. 처리 후 주소창을 정리한다.
 * @returns {Promise<void>}
 */
async function consumeAdminGate() {
  const params = new URLSearchParams(location.search);
  if (!params.has('admin')) return;

  const value = params.get('admin') || '';
  const clean = () => {
    params.delete('admin');
    const qs = params.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
  };

  if (value === 'off') {
    setAdminUnlocked(false);
    clearAdminToken();
    clean();
    toast('이 기기에서 관리자 버튼을 감췄습니다.', 'ok');
    return;
  }

  if (!crypto.subtle) {
    clean();
    toast('보안 연결(https 또는 localhost)에서만 열 수 있습니다.', 'err');
    return;
  }

  try {
    if (await sha256Hex(value) === ADMIN_GATE_HASH) {
      setAdminUnlocked(true);
      clean();
      toast('이 기기에서 관리자 버튼이 보입니다.', 'ok');
      return;
    }
  } catch (e) {
    /* 아래 공통 실패로 떨어진다 */
  }
  clean();
  toast('올바르지 않은 주소입니다.', 'err');
}

let _noticeToastTimer = null;

/**
 * 화면 하단에 성공/실패 메시지를 잠깐 노출한다.
 * @param {string} message
 * @param {'ok' | 'err'} [kind]
 * @returns {void}
 */
function toast(message, kind = 'ok') {
  let el = document.getElementById('notice-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'notice-toast';
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = 'notice-toast notice-toast--' + kind;
  el.hidden = false;
  clearTimeout(_noticeToastTimer);
  _noticeToastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

/**
 * fetch 래퍼.
 * - body 가 있으면 JSON.stringify 하고 Content-Type: application/json 을 붙인다.
 * - adminToken() 이 있으면 Authorization: Bearer 헤더를 붙인다.
 * - 응답 봉투의 ok 가 false 면 error 문자열로 예외를 던진다.
 * - HTTP 401 이면 토큰이 만료된 것이므로 clearAdminToken() 후 예외를 던진다.
 * - 네트워크 실패도 사람이 읽을 수 있는 메시지로 변환한다.
 * @param {'GET' | 'POST' | 'PUT' | 'DELETE'} method
 * @param {string} path
 * @param {unknown} [body]
 * @returns {Promise<unknown>} 응답 봉투의 data
 */
async function api(method, path, body) {
  const headers = {};
  const token = adminToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;

  const init = { method, headers };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error('네트워크 오류: 서버에 연결할 수 없습니다.');
  }

  if (res.status === 401) {
    clearAdminToken();
  }

  let payload;
  try {
    payload = await res.json();
  } catch (e) {
    throw new Error('서버 응답을 해석할 수 없습니다. (HTTP ' + res.status + ')');
  }

  if (!payload || payload.ok !== true) {
    const message = (payload && payload.error) || ('요청이 실패했습니다. (HTTP ' + res.status + ')');
    throw new Error(message);
  }

  return payload.data;
}

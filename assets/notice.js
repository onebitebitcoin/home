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
  const token = sessionStorage.getItem(NOTICE_TOKEN_KEY);
  const expiresAt = sessionStorage.getItem(NOTICE_TOKEN_EXP_KEY);
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
  sessionStorage.setItem(NOTICE_TOKEN_KEY, token);
  sessionStorage.setItem(NOTICE_TOKEN_EXP_KEY, expiresAt);
}

/** @returns {void} */
function clearAdminToken() {
  sessionStorage.removeItem(NOTICE_TOKEN_KEY);
  sessionStorage.removeItem(NOTICE_TOKEN_EXP_KEY);
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

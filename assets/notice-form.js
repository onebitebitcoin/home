/* ============================================================
   공지 작성/수정 페이지 로직 (notice-form.html 전용)
   ============================================================ */
(function () {
  if (!adminToken()) {
    location.replace('/notice');
    return;
  }

  /** @returns {{mode: 'create', id: null} | {mode: 'edit', id: number} | null} */
  function parseRoute() {
    if (/^\/notice\/new\/?$/.test(location.pathname)) {
      return { mode: 'create', id: null };
    }
    const editMatch = location.pathname.match(/^\/notice\/(\d+)\/edit\/?$/);
    if (editMatch) {
      return { mode: 'edit', id: parseInt(editMatch[1], 10) };
    }
    return null;
  }

  const route = parseRoute();
  const titleInput = document.getElementById('notice-title-input');
  const bodyInput = document.getElementById('notice-body-input');
  const titleCount = document.getElementById('title-char-count');
  const bodyCount = document.getElementById('body-char-count');
  const form = document.getElementById('notice-form');
  const stateMsg = document.getElementById('state-msg');

  /**
   * @param {string} message
   * @returns {void}
   */
  function showError(message) {
    stateMsg.textContent = message;
    stateMsg.className = 'state-msg state-msg--err';
    stateMsg.hidden = false;
  }

  /** @returns {void} */
  function updateCounts() {
    titleCount.textContent = `${titleInput.value.length} / 200`;
    bodyCount.textContent = `${bodyInput.value.length} / 10000`;
  }
  titleInput.addEventListener('input', updateCounts);
  bodyInput.addEventListener('input', updateCounts);

  document.getElementById('btn-cancel').addEventListener('click', () => {
    history.back();
  });

  /**
   * @param {number} id
   * @returns {Promise<void>}
   */
  async function loadForEdit(id) {
    try {
      const notice = await api('GET', `/api/notices/${id}`);
      titleInput.value = notice.title;
      bodyInput.value = notice.body;
      updateCounts();
    } catch (e) {
      showError('공지를 불러오지 못했습니다: ' + e.message);
      form.hidden = true;
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const title = titleInput.value.trim();
    const body = bodyInput.value.trim();

    if (!title || title.length > 200) {
      toast('제목은 1~200자로 입력해주세요.', 'err');
      return;
    }
    if (!body || body.length > 10000) {
      toast('내용은 1~10,000자로 입력해주세요.', 'err');
      return;
    }

    try {
      if (route.mode === 'create') {
        const data = await api('POST', '/api/notices', { title, body });
        toast('공지가 등록되었습니다.', 'ok');
        location.href = `/notice/${data.id}`;
      } else {
        await api('PUT', `/api/notices/${route.id}`, { title, body });
        toast('공지가 수정되었습니다.', 'ok');
        location.href = `/notice/${route.id}`;
      }
    } catch (e) {
      toast('저장 실패: ' + e.message, 'err');
    }
  });

  if (!route) {
    showError('잘못된 접근입니다.');
    form.hidden = true;
  } else {
    document.getElementById('form-title').textContent = route.mode === 'create' ? '공지 작성' : '공지 수정';
    if (route.mode === 'edit') loadForEdit(route.id);
  }
})();

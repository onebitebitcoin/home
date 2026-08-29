/* ============================================================
   공지사항 상세 페이지 로직 (notice-detail.html 전용)
   ============================================================ */
(function () {
  /** @returns {number | null} */
  function getNoticeId() {
    const match = location.pathname.match(/^\/notice\/(\d+)\/?$/);
    return match ? parseInt(match[1], 10) : null;
  }

  const noticeId = getNoticeId();

  /**
   * @param {string} message
   * @returns {void}
   */
  function showError(message) {
    const msg = document.getElementById('state-msg');
    msg.textContent = message;
    msg.className = 'state-msg state-msg--err';
    msg.hidden = false;
  }

  /**
   * 관리자 토큰이 있을 때만 수정/삭제 버튼을 그린다.
   * @param {{id: number}} notice
   * @returns {void}
   */
  function renderHeaderActions(notice) {
    const container = document.getElementById('header-actions');
    const token = adminToken();
    if (!token) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = `
      <a class="btn btn-ghost" href="/notice/${notice.id}/edit">수정</a>
      <button type="button" class="btn btn-danger" id="btn-delete-notice">삭제</button>
    `;
    document.getElementById('btn-delete-notice').addEventListener('click', async () => {
      if (!confirm('정말 삭제하시겠습니까?')) return;
      try {
        await api('DELETE', `/api/notices/${notice.id}`);
        toast('공지가 삭제되었습니다.', 'ok');
        location.href = '/notice';
      } catch (e) {
        toast('삭제 실패: ' + e.message, 'err');
      }
    });
  }

  /**
   * @param {{id: number, title: string, body: string, created_at: string, updated_at: string}} notice
   * @returns {void}
   */
  function renderNotice(notice) {
    document.getElementById('notice-title').textContent = notice.title;
    document.getElementById('notice-date').textContent = fmtDate(notice.created_at);
    document.getElementById('notice-edited').hidden = notice.updated_at === notice.created_at;
    document.getElementById('notice-body').innerHTML = esc(notice.body);
    document.getElementById('notice-detail').hidden = false;
    renderHeaderActions(notice);
  }

  /**
   * @param {Array<{id: number, nickname: string, body: string, created_at: string}>} items
   * @returns {void}
   */
  function renderComments(items) {
    const list = document.getElementById('comment-list');
    document.getElementById('comments-count').textContent = String(items.length);

    if (items.length === 0) {
      list.innerHTML = '<li class="comment-empty">첫 댓글을 남겨보세요.</li>';
      return;
    }

    list.innerHTML = items.map((c) => `
      <li class="comment-row">
        <div class="comment-head">
          <span class="comment-nickname">${esc(c.nickname)}</span>
          <span class="comment-date">${fmtDate(c.created_at)}</span>
          <button type="button" class="btn-link comment-delete" data-id="${c.id}">삭제</button>
        </div>
        <div class="comment-body">${esc(c.body)}</div>
      </li>
    `).join('');

    list.querySelectorAll('.comment-delete').forEach((btn) => {
      btn.addEventListener('click', () => deleteComment(parseInt(btn.dataset.id, 10)));
    });
  }

  /** @returns {Promise<void>} */
  async function loadComments() {
    try {
      const data = await api('GET', `/api/notices/${noticeId}/comments`);
      renderComments(data.items);
    } catch (e) {
      document.getElementById('comment-list').innerHTML =
        `<li class="comment-empty comment-empty--err">댓글을 불러오지 못했습니다: ${esc(e.message)}</li>`;
    }
  }

  /**
   * @param {number} commentId
   * @returns {Promise<void>}
   */
  async function deleteComment(commentId) {
    const token = adminToken();

    if (token) {
      if (!confirm('댓글을 삭제하시겠습니까?')) return;
      try {
        await api('DELETE', `/api/notices/${noticeId}/comments/${commentId}`);
        toast('댓글이 삭제되었습니다.', 'ok');
        await loadComments();
      } catch (e) {
        toast('댓글 삭제 실패: ' + e.message, 'err');
      }
      return;
    }

    const password = prompt('댓글 비밀번호를 입력하세요');
    if (password === null) return;
    try {
      await api('DELETE', `/api/notices/${noticeId}/comments/${commentId}`, { password });
      toast('댓글이 삭제되었습니다.', 'ok');
      await loadComments();
    } catch (e) {
      toast('댓글 삭제 실패: ' + e.message, 'err');
    }
  }

  /** @returns {void} */
  function setupCommentForm() {
    const form = document.getElementById('comment-form');
    const nicknameInput = document.getElementById('comment-nickname');
    const passwordInput = document.getElementById('comment-password');
    const bodyInput = document.getElementById('comment-body');
    const charCount = document.getElementById('comment-char-count');

    bodyInput.addEventListener('input', () => {
      charCount.textContent = `${bodyInput.value.length} / 1000`;
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const nickname = nicknameInput.value.trim();
      const password = passwordInput.value;
      const body = bodyInput.value.trim();

      if (!nickname || nickname.length > 20) {
        toast('닉네임은 1~20자로 입력해주세요.', 'err');
        return;
      }
      if (!password || password.length < 4 || password.length > 64) {
        toast('비밀번호는 4~64자로 입력해주세요.', 'err');
        return;
      }
      if (!body || body.length > 1000) {
        toast('댓글 내용은 1~1,000자로 입력해주세요.', 'err');
        return;
      }

      try {
        await api('POST', `/api/notices/${noticeId}/comments`, { nickname, password, body });
        form.reset();
        charCount.textContent = '0 / 1000';
        toast('댓글이 등록되었습니다.', 'ok');
        await loadComments();
      } catch (e) {
        toast('댓글 등록 실패: ' + e.message, 'err');
      }
    });
  }

  /** @returns {Promise<void>} */
  async function init() {
    if (noticeId === null) {
      showError('잘못된 공지 주소입니다.');
      return;
    }

    const msg = document.getElementById('state-msg');
    msg.textContent = '불러오는 중입니다...';
    msg.className = 'state-msg';
    msg.hidden = false;

    try {
      const notice = await api('GET', `/api/notices/${noticeId}`);
      msg.hidden = true;
      renderNotice(notice);
      document.getElementById('comments-section').hidden = false;
      setupCommentForm();
      await loadComments();
    } catch (e) {
      showError('공지를 불러오지 못했습니다: ' + e.message);
    }
  }

  init();
})();

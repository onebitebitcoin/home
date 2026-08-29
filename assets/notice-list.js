/* ============================================================
   공지사항 목록 페이지 로직 (notice.html 전용)
   ============================================================ */
(function () {
  const LIMIT = 20;

  /** @returns {number} */
  function getPage() {
    const params = new URLSearchParams(location.search);
    const page = parseInt(params.get('page'), 10);
    return Number.isInteger(page) && page > 0 ? page : 1;
  }

  /**
   * 관리자 로그인 상태에 따라 우측 상단 버튼 영역을 다시 그린다.
   * @returns {void}
   */
  function renderHeaderActions() {
    const container = document.getElementById('header-actions');
    const token = adminToken();

    if (token) {
      container.innerHTML = `
        <a class="btn btn-primary" href="/notice/new">글쓰기</a>
        <button type="button" class="btn btn-ghost" id="btn-admin-logout">관리자 해제</button>
      `;
      document.getElementById('btn-admin-logout').addEventListener('click', () => {
        clearAdminToken();
        toast('관리자 인증이 해제되었습니다.', 'ok');
        renderHeaderActions();
      });
      return;
    }

    container.innerHTML = `<button type="button" class="btn btn-ghost" id="btn-admin-toggle">관리자</button>`;
    document.getElementById('btn-admin-toggle').addEventListener('click', () => {
      const form = document.getElementById('admin-login');
      form.hidden = !form.hidden;
      if (!form.hidden) document.getElementById('admin-password').focus();
    });
  }

  /**
   * 관리자 비밀번호 입력 폼 동작을 연결한다.
   * @returns {void}
   */
  function setupAdminLogin() {
    const form = document.getElementById('admin-login');
    const input = document.getElementById('admin-password');
    const msg = document.getElementById('admin-login-msg');
    const submit = document.getElementById('admin-login-submit');
    const cancel = document.getElementById('admin-login-cancel');

    async function doLogin() {
      const password = input.value;
      if (!password) {
        msg.textContent = '비밀번호를 입력해주세요.';
        msg.hidden = false;
        return;
      }
      try {
        const data = await api('POST', '/api/notices/auth', { password });
        setAdminToken(data.token, data.expires_at);
        form.hidden = true;
        input.value = '';
        msg.hidden = true;
        toast('관리자 인증에 성공했습니다.', 'ok');
        renderHeaderActions();
      } catch (e) {
        msg.textContent = e.message;
        msg.hidden = false;
      }
    }

    submit.addEventListener('click', doLogin);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') doLogin();
    });
    cancel.addEventListener('click', () => {
      form.hidden = true;
      input.value = '';
      msg.hidden = true;
    });
  }

  /**
   * @param {number} page
   * @param {number} total
   * @returns {void}
   */
  function renderPagination(page, total) {
    const nav = document.getElementById('pagination');
    const totalPages = Math.max(1, Math.ceil(total / LIMIT));
    if (totalPages <= 1) {
      nav.hidden = true;
      nav.innerHTML = '';
      return;
    }
    const prevHref = page > 1 ? `/notice?page=${page - 1}` : null;
    const nextHref = page < totalPages ? `/notice?page=${page + 1}` : null;
    nav.innerHTML = `
      ${prevHref ? `<a class="btn btn-ghost" href="${prevHref}">이전</a>` : `<span class="btn btn-ghost btn-disabled">이전</span>`}
      <span class="pagination-info">${page} / ${totalPages}</span>
      ${nextHref ? `<a class="btn btn-ghost" href="${nextHref}">다음</a>` : `<span class="btn btn-ghost btn-disabled">다음</span>`}
    `;
    nav.hidden = false;
  }

  /**
   * @param {Array<{id: number, title: string, created_at: string, comment_count: number}>} items
   * @returns {void}
   */
  function renderList(items) {
    const list = document.getElementById('notice-list');
    const msg = document.getElementById('state-msg');

    if (items.length === 0) {
      list.innerHTML = '';
      msg.textContent = '등록된 공지가 없습니다.';
      msg.className = 'state-msg';
      msg.hidden = false;
      return;
    }

    msg.hidden = true;
    list.innerHTML = items.map((item) => `
      <li class="notice-row">
        <a class="notice-link" href="/notice/${item.id}">
          <span class="notice-title">${esc(item.title)}</span>
          <span class="notice-meta">
            <span class="notice-date">${fmtDate(item.created_at)}</span>
            <span class="notice-comment-count">댓글 ${item.comment_count}</span>
          </span>
        </a>
      </li>
    `).join('');
  }

  /** @returns {Promise<void>} */
  async function load() {
    const page = getPage();
    const msg = document.getElementById('state-msg');
    msg.textContent = '불러오는 중입니다...';
    msg.className = 'state-msg';
    msg.hidden = false;

    try {
      const data = await api('GET', `/api/notices?page=${page}&limit=${LIMIT}`);
      renderList(data.items);
      renderPagination(data.page, data.total);
    } catch (e) {
      msg.textContent = '공지 목록을 불러오지 못했습니다: ' + e.message;
      msg.className = 'state-msg state-msg--err';
      msg.hidden = false;
      document.getElementById('notice-list').innerHTML = '';
      document.getElementById('pagination').hidden = true;
    }
  }

  renderHeaderActions();
  setupAdminLogin();
  load();
})();

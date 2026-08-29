"""공지사항 DB 접근 계층: stdlib sqlite3 만 사용, ORM 없음.

모든 쿼리는 ? 파라미터 바인딩으로 작성한다 (문자열 조합으로 SQL 만들지 않는다).
ThreadingHTTPServer 위에서 돌기 때문에 커넥션을 모듈 전역으로 공유하지 않고
요청마다 새로 열고 finally 로 닫는다 (sqlite3 커넥션은 여러 스레드에서
동시에 쓰기엔 안전하지 않다).
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get('NOTICE_DB', 'notices.db')


def _now() -> str:
    """UTC ISO8601 문자열 ('Z' 표기). 한국 시간 변환은 클라이언트 쪽에서 한다."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


@contextmanager
def _connect():
    """요청 하나당 커넥션 하나. 정상 종료 시 commit, 예외 시 rollback.

    foreign_keys 는 sqlite 에서 커넥션 단위 설정이라 매번 다시 켜야
    comments 의 ON DELETE CASCADE 가 실제로 작동한다.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """테이블/인덱스가 없으면 생성한다. 서버 시작 시 한 번 호출."""
    with _connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                notice_id     INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
                nickname      TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                body          TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_comments_notice ON comments(notice_id, id)'
        )


def list_notices(page: int, limit: int) -> tuple[list[dict], int]:
    """공지 목록을 최신순(id DESC)으로 반환한다. (items, total).

    댓글 수는 LEFT JOIN + COUNT 로 한 번에 계산한다 (N+1 쿼리 방지).
    """
    offset = (page - 1) * limit
    with _connect() as conn:
        total = conn.execute('SELECT COUNT(*) FROM notices').fetchone()[0]
        rows = conn.execute('''
            SELECT n.id, n.title, n.created_at, COUNT(c.id) AS comment_count
            FROM notices n
            LEFT JOIN comments c ON c.notice_id = n.id
            GROUP BY n.id
            ORDER BY n.id DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
        items = [dict(row) for row in rows]
    return items, total


def get_notice(notice_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, title, body, created_at, updated_at FROM notices WHERE id = ?',
            (notice_id,)
        ).fetchone()
    return dict(row) if row else None


def create_notice(title: str, body: str) -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            'INSERT INTO notices (title, body, created_at, updated_at) VALUES (?, ?, ?, ?)',
            (title, body, now, now)
        )
        new_id = cur.lastrowid
    return new_id


def update_notice(notice_id: int, title: str, body: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            'UPDATE notices SET title = ?, body = ?, updated_at = ? WHERE id = ?',
            (title, body, _now(), notice_id)
        )
        updated = cur.rowcount > 0
    return updated


def delete_notice(notice_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute('DELETE FROM notices WHERE id = ?', (notice_id,))
        deleted = cur.rowcount > 0
    return deleted


def list_comments(notice_id: int) -> list[dict]:
    """오래된순(id ASC). password_hash 는 절대 포함하지 않는다."""
    with _connect() as conn:
        rows = conn.execute(
            'SELECT id, nickname, body, created_at FROM comments '
            'WHERE notice_id = ? ORDER BY id ASC',
            (notice_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def create_comment(notice_id: int, nickname: str, password_hash: str, body: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            'INSERT INTO comments (notice_id, nickname, password_hash, body, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (notice_id, nickname, password_hash, body, _now())
        )
        new_id = cur.lastrowid
    return new_id


def get_comment(comment_id: int) -> dict | None:
    """password_hash 를 포함하는 유일한 조회 함수 (삭제 시 비밀번호 검증용)."""
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, notice_id, nickname, body, created_at, password_hash '
            'FROM comments WHERE id = ?',
            (comment_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_comment(comment_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
        deleted = cur.rowcount > 0
    return deleted

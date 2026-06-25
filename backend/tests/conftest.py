"""Test configuration and fixtures."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import app
from app.models import *  # noqa: F401,F403

from app.config import settings

# Redirect uploads to a temp dir for tests
_tmp_upload = Path(tempfile.mkdtemp(prefix="ctest_"))
settings.upload_dir = str(_tmp_upload)

# Force mock providers in tests (GPU services not available in CI/local)
settings.ocr_provider = "mock"
settings.llm_provider = "mock"

# Use SQLite for tests
_tmp_db = Path(tempfile.mkdtemp(prefix="ctest_db_")) / "test.db"
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{_tmp_db}"

# connect_args: allow concurrent writers (e.g. dual-track pipeline gather) to
# serialize via a busy timeout rather than failing with "database is locked".
# WAL mode permits a writer to proceed alongside readers/other committed state.
test_engine = create_async_engine(
    TEST_DATABASE_URL, echo=False,
    connect_args={"timeout": 30, "check_same_thread": False},
)


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db():
    from collections.abc import AsyncGenerator
    async with test_session_factory() as session:
        yield session
        await session.commit()


from app.database import get_db
app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def tmp_upload_dir():
    """Provide the temp upload directory (also used indirectly via settings)."""
    return _tmp_upload


@pytest.fixture
def sample_pdf_content() -> bytes:
    """Minimal valid PDF content for testing."""
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer
<< /Size 4 /Root 1 0 R >>
startxref
190
%%EOF"""


# ---------------------------------------------------------------------------
# Eval tests: skipped unless explicitly selected with `-m eval`.
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(config, items):
    marker_expr = config.getoption("-m") or ""
    eval_selected = "eval" in marker_expr
    skip_eval = pytest.mark.skip(
        reason="eval tests run only with -m eval (need real/configured providers)",
    )
    for item in items:
        # Use the explicit marker — NOT membership in ``item.keywords``,
        # which also contains the package path ("tests.eval.…") and would
        # wrongly skip the pure metric unit tests in tests/eval/.
        if item.get_closest_marker("eval") is not None and not eval_selected:
            item.add_marker(skip_eval)

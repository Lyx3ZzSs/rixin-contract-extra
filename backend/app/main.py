"""FastAPI application factory."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings

logger = logging.getLogger(__name__)


async def _run_alembic_upgrade(engine) -> None:
    """Run ``alembic upgrade head`` against the configured database.

    Uses the engine's URL so the in-process app and Alembic agree on the target
    DB. Synchronous Alembic API is run in a worker thread.
    """
    import asyncio
    from io import StringIO

    from alembic import command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy.engine import URL as DBURL

    here = Path(__file__).resolve().parent.parent  # backend/
    cfg = AlembicConfig(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    url = str(engine.url)
    # Escape % for configparser interpolation (passwords with URL-encoded chars).
    if isinstance(engine.url, DBURL):
        url = engine.url.render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    buf = StringIO()
    cfg.print_stdout = False

    def _upgrade() -> None:
        cfg.attributes["connection"] = None  # let Alembic open its own
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)
    log_tail = buf.getvalue()
    if log_tail:
        logger.info("Alembic upgrade output:\n%s", log_tail)

# Default field definitions for seeding
_DEFAULT_FIELDS: list[dict] = [
    {"field_key": "party-a-name", "field_name": "甲方名称", "description": "甲方名称", "required": True, "sort_order": 1},
    {"field_key": "party-a-legal-rep", "field_name": "甲方法定代表人", "description": "甲方法定代表人姓名", "sort_order": 2},
    {"field_key": "party-a-agent", "field_name": "甲方委托代理人", "description": "甲方委托代理人姓名", "sort_order": 3},
    {"field_key": "party-a-address", "field_name": "甲方通讯地址", "description": "甲方通讯地址", "sort_order": 4},
    {"field_key": "party-a-bank", "field_name": "甲方开户行", "description": "甲方开户银行名称", "sort_order": 5},
    {"field_key": "party-a-account", "field_name": "甲方账号", "description": "甲方银行账号", "sort_order": 6},
    {"field_key": "party-a-tax", "field_name": "甲方税号", "description": "甲方纳税人识别号", "sort_order": 7},
    {"field_key": "party-a-phone", "field_name": "甲方电话", "description": "甲方联系电话", "sort_order": 8},
    {"field_key": "party-b-name", "field_name": "乙方名称", "description": "乙方名称", "required": True, "sort_order": 9},
    {"field_key": "party-b-legal-rep", "field_name": "乙方法定代表人", "description": "乙方法定代表人姓名", "sort_order": 10},
    {"field_key": "party-b-agent", "field_name": "乙方委托代理人", "description": "乙方委托代理人姓名", "sort_order": 11},
    {"field_key": "party-b-address", "field_name": "乙方通讯地址", "description": "乙方通讯地址", "sort_order": 12},
    {"field_key": "party-b-bank", "field_name": "乙方开户行", "description": "乙方开户银行名称", "sort_order": 13},
    {"field_key": "party-b-account", "field_name": "乙方账号", "description": "乙方银行账号", "sort_order": 14},
    {"field_key": "party-b-tax", "field_name": "乙方税号", "description": "乙方纳税人识别号", "sort_order": 15},
    {"field_key": "party-b-phone", "field_name": "乙方电话", "description": "乙方联系电话", "sort_order": 16},
    # -- 金融类 --
    {"field_key": "contract-amount", "field_name": "合同金额", "description": "合同总金额，包括币种和数额", "value_type": "string", "sort_order": 17},
    {"field_key": "prepayment-amount", "field_name": "预付款金额", "description": "合同约定的预付款/首期款金额，优先提取具体金额数值，其次提取比例", "value_type": "string", "sort_order": 18},
    {"field_key": "prepayment-ratio", "field_name": "预付款比例", "description": "预付款占合同总金额的比例", "value_type": "string", "sort_order": 19},
    {"field_key": "payment-method", "field_name": "付款方式", "description": "合同约定的付款方式，如银行转账、支票等", "value_type": "string", "sort_order": 20},
    # -- 日期类 --
    {"field_key": "sign-date", "field_name": "签署日期", "description": "合同签署日期", "value_type": "string", "sort_order": 21},
    {"field_key": "effective-date", "field_name": "生效日期", "description": "合同生效日期", "value_type": "string", "sort_order": 22},
    {"field_key": "end-date", "field_name": "终止日期", "description": "合同终止日期或截止日期", "value_type": "string", "sort_order": 23},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: run Alembic migrations to the latest revision, then seed default
    # fields. We intentionally do NOT use Base.metadata.create_all here: it would
    # create all tables but leave alembic_version empty, so subsequent
    # `alembic upgrade` runs would fail to reconcile. Migrations are the single
    # source of truth for schema.
    from app.database import engine
    try:
        await _run_alembic_upgrade(engine)
    except Exception:
        logging.getLogger(__name__).error(
            "Alembic upgrade failed on startup — app will start but the schema "
            "may be incomplete. Run `alembic upgrade head` manually.",
            exc_info=True,
        )

    # Seed default field definitions — incremental sync
    try:
        from app.database import async_session_factory
        from app.models.field_definition import FieldDefinition
        from sqlalchemy import select
        async with async_session_factory() as db:
            added = 0
            reactivated = 0
            for f in _DEFAULT_FIELDS:
                existing = await db.execute(
                    select(FieldDefinition).where(FieldDefinition.field_key == f["field_key"])
                )
                obj = existing.scalar_one_or_none()
                if obj is None:
                    db.add(FieldDefinition(**f))
                    added += 1
                elif not obj.is_active:
                    # Reactivate previously soft-deleted default fields
                    for k, v in f.items():
                        setattr(obj, k, v)
                    obj.is_active = True
                    reactivated += 1
            if added or reactivated:
                await db.commit()
                if added:
                    logger.info("Synced %d new field definition(s)", added)
                if reactivated:
                    logger.info("Reactivated %d field definition(s)", reactivated)
    except Exception:
        logging.getLogger(__name__).error(
            "Field definition seeding failed — app will start without defaults",
            exc_info=True,
        )

    if not settings.app_api_keys:
        logger.warning(
            "AUTH DISABLED: APP_API_KEYS is empty — all /api/v1 routes are open. "
            "Set APP_API_KEYS in production to require an X-API-Key header."
        )

    from app.worker import run_worker
    worker_task = asyncio.create_task(run_worker())
    app.state.task_worker_task = worker_task
    logger.info("Embedded task worker started")
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("Embedded task worker stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

_origins = (
    [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    if settings.allowed_origins else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=bool(settings.allowed_origins),  # credentials only with explicit origins
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8010,
        reload=settings.debug,
    )

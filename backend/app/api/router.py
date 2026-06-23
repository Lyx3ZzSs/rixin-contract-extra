from fastapi import APIRouter, Depends

from app.api.contract import router as contract_router
from app.api.deps import verify_api_key
from app.api.field_definition import router as field_def_router
from app.api.review import router as review_router
from app.api.task import router as task_router

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])
api_router.include_router(contract_router)
api_router.include_router(field_def_router)
api_router.include_router(task_router)
api_router.include_router(review_router)

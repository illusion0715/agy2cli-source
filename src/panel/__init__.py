"""Only reviewed login/static routes are wired in this archive."""
from fastapi import APIRouter
from src.panel.auth import router as auth_router
from src.panel.root import router as root_router
from src.panel.version import router as version_router
router = APIRouter()
router.include_router(auth_router)
router.include_router(root_router)
router.include_router(version_router)

from fastapi import APIRouter

from .. import __version__
from ..channels import KNOWN_CHANNELS
from ..distributor import list_workers

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/channels", response_model=list[str])
def channels():
    return list(KNOWN_CHANNELS)


@router.get("/workers")
def workers():
    """Кто недавно забирал задачи. Реестр живёт в памяти этого процесса."""
    return list_workers()

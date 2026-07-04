from fastapi import APIRouter

from .. import __version__
from ..channels.registry import available_channels

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/channels", response_model=list[str])
def channels():

    return available_channels()

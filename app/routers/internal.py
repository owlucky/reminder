from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..channels import is_known_channel
from ..config import get_settings
from ..database import get_db
from ..distributor import complete, lease_next

router = APIRouter(prefix="/internal", tags=["internal"])


class WorkerPoll(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=100)
    channel: str = Field(..., max_length=50)
    route_key: str | None = Field(default=None, max_length=200)


class TaskResultIn(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=100)
    success: bool
    message: str | None = None


def require_internal(
    x_internal_token: str | None = Header(default=None),
) -> None:
    expected = get_settings().internal_token
    if expected and x_internal_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный внутренний токен")


def _task_payload(task) -> dict:
    return {
        "id": task.id,
        "channel": task.channel,
        "address": task.address,
        "title": task.title,
        "body": task.body,
        "kind": task.kind,
        "route_key": task.route_key,
    }


@router.post("/tasks/next", dependencies=[Depends(require_internal)])
def next_task(payload: WorkerPoll, db: Session = Depends(get_db)):
    if not is_known_channel(payload.channel):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Канал '{payload.channel}' не поддерживается",
        )
    task = lease_next(db, payload.worker_id, payload.channel, payload.route_key)
    if task is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _task_payload(task)


@router.post("/tasks/{task_id}/result", dependencies=[Depends(require_internal)])
def task_result(
    task_id: int,
    payload: TaskResultIn,
    db: Session = Depends(get_db),
):
    try:
        task = complete(
            db, task_id, payload.worker_id, payload.success, payload.message
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена") from None
    except PermissionError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Задача не арендована этим воркером",
        ) from None
    return {"id": task.id, "status": task.status}

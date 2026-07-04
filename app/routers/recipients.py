from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..channels.registry import is_registered
from ..database import get_db
from ..models import Recipient
from ..schemas import RecipientCreate, RecipientRead, RecipientUpdate

router = APIRouter(prefix="/recipients", tags=["recipients"])


def _get_or_404(db: Session, recipient_id: int) -> Recipient:
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Получатель не найден")
    return recipient


def _check_channel(channel: str) -> None:
    if not is_registered(channel):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Канал '{channel}' не зарегистрирован",
        )


@router.post("", response_model=RecipientRead, status_code=status.HTTP_201_CREATED)
def create_recipient(payload: RecipientCreate, db: Session = Depends(get_db)):
    _check_channel(payload.channel)
    recipient = Recipient(**payload.model_dump())
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return recipient


@router.get("", response_model=list[RecipientRead])
def list_recipients(db: Session = Depends(get_db)):
    return db.execute(select(Recipient)).scalars().all()


@router.get("/{recipient_id}", response_model=RecipientRead)
def get_recipient(recipient_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, recipient_id)


@router.patch("/{recipient_id}", response_model=RecipientRead)
def update_recipient(
    recipient_id: int, payload: RecipientUpdate, db: Session = Depends(get_db)
):
    recipient = _get_or_404(db, recipient_id)
    data = payload.model_dump(exclude_unset=True)
    if "channel" in data and data["channel"] is not None:
        _check_channel(data["channel"])
    for key, value in data.items():
        setattr(recipient, key, value)
    db.commit()
    db.refresh(recipient)
    return recipient


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipient(recipient_id: int, db: Session = Depends(get_db)):
    recipient = _get_or_404(db, recipient_id)
    db.delete(recipient)
    db.commit()

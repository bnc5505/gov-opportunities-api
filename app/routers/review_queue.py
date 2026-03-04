from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List
from datetime import datetime

from database import get_db
from models import ReviewQueue
from schemas import ReviewQueueResponse, ReviewQueueUpdate

router = APIRouter(prefix="/review-queue", tags=["Review Queue"])


@router.get("", response_model=List[ReviewQueueResponse])
def list_review_queue(
    status: str = Query("pending", description="Filter by review_status"),
    db: Session = Depends(get_db),
):
    return (
        db.query(ReviewQueue)
        .options(joinedload(ReviewQueue.opportunity))
        .filter(ReviewQueue.review_status == status)
        .order_by(ReviewQueue.created_at.asc())
        .all()
    )


@router.put("/{item_id}", response_model=ReviewQueueResponse)
def update_review_item(item_id: int, payload: ReviewQueueUpdate, db: Session = Depends(get_db)):
    item = (
        db.query(ReviewQueue)
        .options(joinedload(ReviewQueue.opportunity))
        .filter(ReviewQueue.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Review queue item not found")

    item.review_status = payload.review_status
    item.reviewer_notes = payload.reviewer_notes
    # stamp reviewed_at when moving out of pending
    if payload.review_status != "pending":
        item.reviewed_at = datetime.utcnow()

    db.commit()
    db.refresh(item)
    return item

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from models import User, SavedOpportunity, SavedSearch, Opportunity
from schemas import (
    SavedOpportunityCreate, SavedOpportunityResponse,
    SavedSearchCreate, SavedSearchResponse,
)

router = APIRouter(prefix="/users", tags=["Saved"])


def _get_user_or_404(user_id: int, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Saved Opportunities ──────────────────────────────────────────────────────

@router.get("/{user_id}/saved-opportunities", response_model=List[SavedOpportunityResponse])
def list_saved_opportunities(user_id: int, db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    return (
        db.query(SavedOpportunity)
        .options(joinedload(SavedOpportunity.opportunity).joinedload(Opportunity.agency),
                 joinedload(SavedOpportunity.opportunity).joinedload(Opportunity.state))
        .filter(SavedOpportunity.user_id == user_id)
        .all()
    )


@router.post("/{user_id}/saved-opportunities", response_model=SavedOpportunityResponse, status_code=201)
def save_opportunity(user_id: int, payload: SavedOpportunityCreate, db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    opp = db.query(Opportunity).filter(Opportunity.id == payload.opportunity_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    existing = (
        db.query(SavedOpportunity)
        .filter(SavedOpportunity.user_id == user_id, SavedOpportunity.opportunity_id == payload.opportunity_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Opportunity already saved")
    saved = SavedOpportunity(user_id=user_id, opportunity_id=payload.opportunity_id, notes=payload.notes)
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.delete("/{user_id}/saved-opportunities/{saved_id}", status_code=204)
def unsave_opportunity(user_id: int, saved_id: int, db: Session = Depends(get_db)):
    saved = (
        db.query(SavedOpportunity)
        .filter(SavedOpportunity.id == saved_id, SavedOpportunity.user_id == user_id)
        .first()
    )
    if not saved:
        raise HTTPException(status_code=404, detail="Saved opportunity not found")
    db.delete(saved)
    db.commit()


# ── Saved Searches ───────────────────────────────────────────────────────────

@router.get("/{user_id}/saved-searches", response_model=List[SavedSearchResponse])
def list_saved_searches(user_id: int, db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    return db.query(SavedSearch).filter(SavedSearch.user_id == user_id).all()


@router.post("/{user_id}/saved-searches", response_model=SavedSearchResponse, status_code=201)
def create_saved_search(user_id: int, payload: SavedSearchCreate, db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    search = SavedSearch(user_id=user_id, **payload.model_dump())
    db.add(search)
    db.commit()
    db.refresh(search)
    return search


@router.put("/{user_id}/saved-searches/{search_id}", response_model=SavedSearchResponse)
def update_saved_search(user_id: int, search_id: int, payload: SavedSearchCreate, db: Session = Depends(get_db)):
    search = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == search_id, SavedSearch.user_id == user_id)
        .first()
    )
    if not search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(search, field, value)
    db.commit()
    db.refresh(search)
    return search


@router.delete("/{user_id}/saved-searches/{search_id}", status_code=204)
def delete_saved_search(user_id: int, search_id: int, db: Session = Depends(get_db)):
    search = (
        db.query(SavedSearch)
        .filter(SavedSearch.id == search_id, SavedSearch.user_id == user_id)
        .first()
    )
    if not search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(search)
    db.commit()

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Literal, Optional
from datetime import datetime

from database import get_db
from models import Opportunity, State
from rate_limit import limiter
from schemas import (
    OpportunityCreate, OpportunityUpdate,
    OpportunityResponse, OpportunityListItem,
    OpportunityFilters,
    PaginatedOpportunityResponse,
)

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])


def _build_query(db, f: OpportunityFilters):
    q = db.query(Opportunity).options(
        joinedload(Opportunity.agency),
        joinedload(Opportunity.state),
        joinedload(Opportunity.categories),
    )
    if f.q:
        q = q.filter(or_(
            Opportunity.title.ilike(f"%{f.q}%"),
            Opportunity.summary.ilike(f"%{f.q}%"),
            Opportunity.description.ilike(f"%{f.q}%"),
        ))
    if f.opportunity_type:
        q = q.filter(Opportunity.opportunity_type == f.opportunity_type)
    if f.status:
        q = q.filter(Opportunity.status == f.status)
    if f.state_code:
        q = q.join(Opportunity.state).filter(State.code == f.state_code.upper())
    if f.rolling is not None:
        q = q.filter(Opportunity.rolling == f.rolling)
    if f.industry:
        q = q.filter(Opportunity.industry.ilike(f"%{f.industry}%"))
    if f.agency_id:
        q = q.filter(Opportunity.agency_id == f.agency_id)
    if f.award_min is not None:
        q = q.filter(Opportunity.award_max >= f.award_min)
    if f.award_max is not None:
        q = q.filter(Opportunity.award_min <= f.award_max)
    if f.deadline_after:
        q = q.filter(Opportunity.deadline >= f.deadline_after)
    if f.deadline_before:
        q = q.filter(Opportunity.deadline <= f.deadline_before)
    if f.eligibility_individual is not None:
        q = q.filter(Opportunity.eligibility_individual == f.eligibility_individual)
    if f.eligibility_organization is not None:
        q = q.filter(Opportunity.eligibility_organization == f.eligibility_organization)
    if f.needs_review is not None:
        q = q.filter(Opportunity.needs_review == f.needs_review)
    return q


@limiter.limit("100/minute")
@router.get("", response_model=PaginatedOpportunityResponse)
def list_opportunities(
    request: Request,
    filters: OpportunityFilters = Depends(),
    sort_by: Literal["deadline", "award_min", "award_max", "created_at", "title"] = Query("deadline"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query    = _build_query(db, filters)
    sort_col = getattr(Opportunity, sort_by, Opportunity.deadline)
    query    = query.order_by(sort_col.asc() if sort_order == "asc" else sort_col.desc())

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return PaginatedOpportunityResponse(
        total=total,
        page=page,
        per_page=per_page,
        total_pages=-(-total // per_page),
        data=items,
    )


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    opp = (
        db.query(Opportunity)
        .options(
            joinedload(Opportunity.agency),
            joinedload(Opportunity.state),
            joinedload(Opportunity.source),
            joinedload(Opportunity.categories),
            joinedload(Opportunity.eligible_applicants),
        )
        .filter(Opportunity.id == opportunity_id)
        .first()
    )
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


@router.post("", response_model=OpportunityResponse, status_code=201)
def create_opportunity(payload: OpportunityCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"category_ids", "applicant_type_ids"})
    opp  = Opportunity(**data)
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return opp


@router.put("/{opportunity_id}", response_model=OpportunityResponse)
def update_opportunity(opportunity_id: int, payload: OpportunityUpdate, db: Session = Depends(get_db)):
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(opp, field, value)
    db.commit()
    db.refresh(opp)
    return opp


@router.delete("/{opportunity_id}", status_code=204)
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    db.delete(opp)
    db.commit()

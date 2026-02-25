from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional
from datetime import datetime, timedelta

from database import get_db
from models import Opportunity, State
from schemas import (
    OpportunityCreate, OpportunityUpdate,
    OpportunityResponse, OpportunityListItem,
    PaginatedOpportunityResponse,
)

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])


def _build_query(db, search, opportunity_type, category, status, state_code,
                 is_global, rolling, industry, agency_id, award_min, award_max,
                 deadline_after, deadline_before, eligibility_individual,
                 eligibility_organization, needs_review,
                 exclude_fee, exclude_equity, exclude_safe_note):
    q = db.query(Opportunity).options(
        joinedload(Opportunity.agency),
        joinedload(Opportunity.state),
        joinedload(Opportunity.categories),
    )
    if search:
        q = q.filter(or_(
            Opportunity.title.ilike(f"%{search}%"),
            Opportunity.summary.ilike(f"%{search}%"),
            Opportunity.description.ilike(f"%{search}%"),
        ))
    if opportunity_type:
        q = q.filter(Opportunity.opportunity_type == opportunity_type)
    if category:
        q = q.filter(Opportunity.category == category)
    if status:
        q = q.filter(Opportunity.status == status)
    if state_code:
        q = q.join(Opportunity.state).filter(State.code == state_code.upper())
    if is_global is not None:
        q = q.filter(Opportunity.is_global == is_global)
    if rolling is not None:
        q = q.filter(Opportunity.rolling == rolling)
    if industry:
        q = q.filter(Opportunity.industry.ilike(f"%{industry}%"))
    if agency_id:
        q = q.filter(Opportunity.agency_id == agency_id)
    if award_min is not None:
        q = q.filter(Opportunity.award_max >= award_min)
    if award_max is not None:
        q = q.filter(Opportunity.award_min <= award_max)
    if deadline_after:
        q = q.filter(Opportunity.deadline >= deadline_after)
    if deadline_before:
        q = q.filter(Opportunity.deadline <= deadline_before)
    if eligibility_individual is not None:
        q = q.filter(Opportunity.eligibility_individual == eligibility_individual)
    if eligibility_organization is not None:
        q = q.filter(Opportunity.eligibility_organization == eligibility_organization)
    if needs_review is not None:
        q = q.filter(Opportunity.needs_review == needs_review)
    if exclude_fee:
        q = q.filter(Opportunity.fee_required == False)
    if exclude_equity:
        q = q.filter(Opportunity.equity_percentage == False)
    if exclude_safe_note:
        q = q.filter(Opportunity.safe_note == False)
    return q


@router.get("", response_model=PaginatedOpportunityResponse)
def list_opportunities(
    q:                        Optional[str]  = Query(None),
    opportunity_type:         Optional[str]  = Query(None),
    category:                 Optional[str]  = Query(None),
    status:                   Optional[str]  = Query(None),
    state_code:               Optional[str]  = Query(None),
    is_global:                Optional[bool] = Query(None),
    rolling:                  Optional[bool] = Query(None),
    industry:                 Optional[str]  = Query(None),
    agency_id:                Optional[int]  = Query(None),
    award_min:                Optional[float]= Query(None),
    award_max:                Optional[float]= Query(None),
    deadline_after:           Optional[datetime] = Query(None),
    deadline_before:          Optional[datetime] = Query(None),
    eligibility_individual:   Optional[bool] = Query(None),
    eligibility_organization: Optional[bool] = Query(None),
    needs_review:             Optional[bool] = Query(None),
    exclude_fee:              bool           = Query(False),
    exclude_equity:           bool           = Query(False),
    exclude_safe_note:        bool           = Query(False),
    sort_by:                  str            = Query("deadline"),
    sort_order:               str            = Query("asc", pattern="^(asc|desc)$"),
    page:                     int            = Query(1, ge=1),
    per_page:                 int            = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = _build_query(
        db, q, opportunity_type, category, status, state_code,
        is_global, rolling, industry, agency_id, award_min, award_max,
        deadline_after, deadline_before, eligibility_individual,
        eligibility_organization, needs_review,
        exclude_fee, exclude_equity, exclude_safe_note,
    )

    sort_col = getattr(Opportunity, sort_by, Opportunity.deadline)
    query    = query.order_by(sort_col.asc() if sort_order == "asc" else sort_col.desc())

    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return PaginatedOpportunityResponse(
        total    = total,
        page     = page,
        per_page = per_page,
        pages    = -(-total // per_page),
        items    = items,
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
    opp = Opportunity(**payload.model_dump())
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

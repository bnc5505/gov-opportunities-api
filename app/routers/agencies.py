from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models import Agency
from schemas import AgencyCreate, AgencyResponse

router = APIRouter(prefix="/agencies", tags=["Agencies"])


@router.get("", response_model=List[AgencyResponse])
def list_agencies(
    level: Optional[str] = Query(None, description="Filter by level: federal, state, local"),
    state_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Agency)
    if level:
        q = q.filter(Agency.level == level)
    if state_id:
        q = q.filter(Agency.state_id == state_id)
    return q.all()


@router.get("/{agency_id}", response_model=AgencyResponse)
def get_agency(agency_id: int, db: Session = Depends(get_db)):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency


@router.post("", response_model=AgencyResponse, status_code=201)
def create_agency(payload: AgencyCreate, db: Session = Depends(get_db)):
    existing = db.query(Agency).filter(Agency.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Agency with code '{payload.code}' already exists")
    agency = Agency(**payload.model_dump())
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return agency


@router.put("/{agency_id}", response_model=AgencyResponse)
def update_agency(agency_id: int, payload: AgencyCreate, db: Session = Depends(get_db)):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(agency, field, value)
    db.commit()
    db.refresh(agency)
    return agency


@router.delete("/{agency_id}", status_code=204)
def delete_agency(agency_id: int, db: Session = Depends(get_db)):
    agency = db.query(Agency).filter(Agency.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    db.delete(agency)
    db.commit()

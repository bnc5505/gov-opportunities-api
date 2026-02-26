from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import State
from schemas import StateCreate, StateResponse

router = APIRouter(prefix="/states", tags=["States"])


@router.get("", response_model=List[StateResponse])
def list_states(db: Session = Depends(get_db)):
    return db.query(State).all()


@router.get("/{state_id}", response_model=StateResponse)
def get_state(state_id: int, db: Session = Depends(get_db)):
    state = db.query(State).filter(State.id == state_id).first()
    if not state:
        raise HTTPException(status_code=404, detail="State not found")
    return state


@router.post("", response_model=StateResponse, status_code=201)
def create_state(payload: StateCreate, db: Session = Depends(get_db)):
    existing = db.query(State).filter(State.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"State with code '{payload.code}' already exists")
    state = State(code=payload.code, name=payload.name)
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


@router.put("/{state_id}", response_model=StateResponse)
def update_state(state_id: int, payload: StateCreate, db: Session = Depends(get_db)):
    state = db.query(State).filter(State.id == state_id).first()
    if not state:
        raise HTTPException(status_code=404, detail="State not found")
    state.code = payload.code
    state.name = payload.name
    db.commit()
    db.refresh(state)
    return state


@router.delete("/{state_id}", status_code=204)
def delete_state(state_id: int, db: Session = Depends(get_db)):
    state = db.query(State).filter(State.id == state_id).first()
    if not state:
        raise HTTPException(status_code=404, detail="State not found")
    db.delete(state)
    db.commit()

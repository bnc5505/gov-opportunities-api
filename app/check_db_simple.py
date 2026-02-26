"""
Check what grants are currently in the Azure PostgreSQL database.
Run this from ~/gov-opportunities-api/app/
"""

from database import SessionLocal
from models import Opportunity, State
from sqlalchemy import func

db = SessionLocal()

try:
    total_grants = db.query(Opportunity).count()
    
    print("="*70)
    print("DATABASE CHECK - GRANTS")
    print("="*70)
    print(f"\nTotal grants: {total_grants}\n")
    
    if total_grants == 0:
        print("Database is empty")
    else:
        grants = db.query(Opportunity).order_by(Opportunity.created_at.desc()).all()
        
        for idx, g in enumerate(grants, 1):
            print(f"{idx}. {g.title[:70]}")
            print(f"   State: {g.state.code if g.state else 'N/A'} | Status: {g.status.value if g.status else 'N/A'}")
            print(f"   Quality: {g.data_quality_score:.0%} | Review: {'Yes' if g.needs_review else 'No'}")
            print(f"   Tags: {len(g.tags or [])} | SDG: {'Yes' if g.sdg_alignment else 'No'}")
            print()
        
        # Stats
        avg_quality = db.query(func.avg(Opportunity.data_quality_score)).scalar()
        with_tags = db.query(Opportunity).filter(Opportunity.tags != None).count()
        with_sdg = db.query(Opportunity).filter(Opportunity.sdg_alignment != None).count()
        
        print("="*70)
        print(f"Avg Quality: {avg_quality:.0%} | With Tags: {with_tags} | With SDG: {with_sdg}")
        print("="*70)
        
finally:
    db.close()
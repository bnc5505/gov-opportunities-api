"""
Database configuration and session management for GovGrants Hub.

This file sets up the connection to the database and provides
helper functions for initializing tables and seeding reference data.

Updated to work with Runwei-aligned schema.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
from config import settings

# Read the database connection string from Key Vault or .env fallback
DATABASE_URL = settings.database_url

# Create the database engine
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False
    )
else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

# SessionLocal is a factory that creates database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Provides a database session to API route handlers.
    The session is automatically closed after the request finishes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Creates all database tables based on the Runwei-aligned models.
    
    WARNING: This drops all existing tables first!
    Only run this on fresh database or when you want to reset everything.
    """
    from models import Base
    from sqlalchemy import text
    
    print("⚠️  WARNING: This will drop all existing tables!")
    print("Dropping existing tables...")
    
    # For PostgreSQL, we need to use CASCADE to drop tables with dependencies
    with engine.connect() as conn:
        if not DATABASE_URL.startswith("sqlite"):
            # PostgreSQL: Drop schema and recreate (cleanest approach)
            print("  Dropping all tables with CASCADE...")
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
            conn.commit()
        else:
            # SQLite: Can just drop normally
            Base.metadata.drop_all(bind=engine)
    
    print("Creating new tables with Runwei-aligned schema...")
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables created successfully")


def seed_initial_data():
    """
    Populates the database with reference data.
    
    Runwei-aligned version includes:
    - 4 pilot states (PA, NY, MD, DC)
    - Key government agencies with logos
    - Initial data sources for scraping
    """
    from models import State, Agency, Source
    
    db = SessionLocal()
    
    try:
        # Check if already seeded
        if db.query(State).count() > 0:
            print("Database already contains reference data, skipping seed")
            return
        
        print("\nSeeding reference data...")
        
        # ============================================================================
        # STATES
        # ============================================================================
        print("  Adding states...")
        states = [
            State(code="PA", name="Pennsylvania"),
            State(code="NY", name="New York"),
            State(code="MD", name="Maryland"),
            State(code="DC", name="Washington DC"),
        ]
        db.add_all(states)
        db.flush()
        
        # Get state IDs for foreign keys
        dc_state = db.query(State).filter(State.code == "DC").first()
        pa_state = db.query(State).filter(State.code == "PA").first()
        ny_state = db.query(State).filter(State.code == "NY").first()
        md_state = db.query(State).filter(State.code == "MD").first()
        
        # ============================================================================
        # AGENCIES
        # ============================================================================
        print("  Adding agencies...")
        agencies = [
            # DC Agencies
            Agency(
                code="OVSJG",
                name="Office of Victim Services and Justice Grants",
                level="local",
                state_id=dc_state.id,
                website_url="https://ovsjg.dc.gov",
                logo_url="https://ovsjg.dc.gov/sites/default/files/dc/sites/ovsjg/logo.png"
            ),
            Agency(
                code="DSLBD",
                name="DC Department of Small and Local Business Development",
                level="local",
                state_id=dc_state.id,
                website_url="https://dslbd.dc.gov",
                logo_url=None  # Will be added when found
            ),
            
            # Pennsylvania Agencies
            Agency(
                code="PA-DCED",
                name="PA Department of Community and Economic Development",
                level="state",
                state_id=pa_state.id,
                website_url="https://dced.pa.gov",
                logo_url=None
            ),
            
            # New York Agencies
            Agency(
                code="NY-ESD",
                name="Empire State Development",
                level="state",
                state_id=ny_state.id,
                website_url="https://esd.ny.gov",
                logo_url=None
            ),
            
            # Maryland Agencies
            Agency(
                code="MD-COMMERCE",
                name="Maryland Department of Commerce",
                level="state",
                state_id=md_state.id,
                website_url="https://commerce.maryland.gov",
                logo_url=None
            ),
            
            # Federal Agencies (common across all states)
            Agency(
                code="HHS",
                name="Department of Health and Human Services",
                level="federal",
                website_url="https://www.hhs.gov",
                logo_url=None
            ),
            Agency(
                code="ED",
                name="Department of Education",
                level="federal",
                website_url="https://www.ed.gov",
                logo_url=None
            ),
            Agency(
                code="NSF",
                name="National Science Foundation",
                level="federal",
                website_url="https://www.nsf.gov",
                logo_url=None
            ),
            Agency(
                code="SBA",
                name="Small Business Administration",
                level="federal",
                website_url="https://www.sba.gov",
                logo_url=None
            ),
        ]
        db.add_all(agencies)
        db.flush()
        
        # Get agency IDs for sources
        ovsjg = db.query(Agency).filter(Agency.code == "OVSJG").first()
        
        # ============================================================================
        # SOURCES (Scraping endpoints)
        # ============================================================================
        print("  Adding data sources...")
        sources = [
            # DC Sources
            Source(
                name="DC OVSJG Current Funding Opportunities",
                url="https://ovsjg.dc.gov/page/funding-opportunities-current",
                state_id=dc_state.id,
                scraper_type="pdf",
                scrape_frequency_hours=24,
                is_active=True
            ),
            Source(
                name="DC Small Business Grants",
                url="https://dslbd.dc.gov/page/grant-programs",
                state_id=dc_state.id,
                scraper_type="html",
                scrape_frequency_hours=24,
                is_active=True
            ),
            
            # Pennsylvania Sources
            Source(
                name="Pennsylvania DCED Programs",
                url="https://dced.pa.gov/programs/",
                state_id=pa_state.id,
                scraper_type="html",
                scrape_frequency_hours=24,
                is_active=True
            ),
            
            # New York Sources
            Source(
                name="New York ESD Funding Opportunities",
                url="https://esd.ny.gov/doing-business-ny/funding-opportunities",
                state_id=ny_state.id,
                scraper_type="html",
                scrape_frequency_hours=24,
                is_active=True
            ),
            
            # Maryland Sources
            Source(
                name="Maryland Commerce Funding",
                url="https://commerce.maryland.gov/fund",
                state_id=md_state.id,
                scraper_type="html",
                scrape_frequency_hours=24,
                is_active=True
            ),
        ]
        db.add_all(sources)
        
        db.commit()
        
        print("\n✓ Reference data seeded successfully!")
        print(f"  - {len(states)} states")
        print(f"  - {len(agencies)} agencies")
        print(f"  - {len(sources)} data sources")
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ Error during seed: {e}")
        raise
    finally:
        db.close()


def reset_database():
    """
    Complete database reset: drop all tables, recreate, and seed.
    Use this when you want a fresh start.
    """
    print("="*60)
    print("DATABASE RESET - RUNWEI-ALIGNED SCHEMA")
    print("="*60)
    
    init_db()
    print()
    seed_initial_data()
    
    print("\n" + "="*60)
    print("✓ Database setup complete!")
    print("="*60)
    print("\nYou now have:")
    print("  - Empty opportunities table (ready for grants)")
    print("  - 4 states configured")
    print("  - 9 agencies configured")
    print("  - 5 scraping sources configured")
    print("  - Review queue ready")
    print("  - Scrape logs ready")
    print("\nNext step: Run the multi-agent system to populate grants!")


if __name__ == "__main__":
    reset_database()

"""
Database configuration and session management for GovGrants Hub.

This file sets up the connection to the database and provides
helper functions for initializing tables and seeding reference data.

We support both SQLite for local development and PostgreSQL for Azure production.
The DATABASE_URL environment variable controls which one gets used.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
import os
from dotenv import load_dotenv

load_dotenv()

# Read the database connection string from the environment.
# If not set, default to a local SQLite file for development.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./gov_grants.db"
)

# Create the database engine.
# For SQLite we need check_same_thread=False because FastAPI uses multiple threads.
# For PostgreSQL we use connection pooling to handle multiple requests efficiently.

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

# SessionLocal is a factory that creates database sessions.
# Each request to the API gets its own session through the get_db function.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Provides a database session to API route handlers.
    The session is automatically closed after the request finishes.
    
    This is used as a FastAPI dependency like this:
    
    @app.get("/opportunities/")
    def get_opportunities(db: Session = Depends(get_db)):
        return db.query(Opportunity).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Creates all database tables based on the models defined in models.py.
    This reads the model definitions and generates the corresponding SQL
    CREATE TABLE statements, then runs them against the database.
    
    Only run this on a fresh database. If tables already exist, use
    Alembic migrations instead to avoid losing data.
    """
    from models import Base
    Base.metadata.create_all(bind=engine)
    print("Database tables created")


def seed_initial_data():
    """
    Populates the database with reference data that the application needs.
    This includes the four pilot states, the 12 opportunity categories,
    federal agencies, and applicant types.
    
    This function is safe to run multiple times. It checks if data already
    exists before inserting anything, so you will not get duplicates.
    """
    from models import State, Agency, Category, ApplicantType
    
    db = SessionLocal()
    
    try:
        # Check if we already ran this. If agencies exist, assume everything is seeded.
        if db.query(Agency).count() > 0:
            print("Database already contains reference data, skipping seed")
            return
        
        # Seed the four pilot states
        states = [
            State(code="PA", name="Pennsylvania", is_active=True),
            State(code="NY", name="New York", is_active=True),
            State(code="MD", name="Maryland", is_active=True),
            State(code="DC", name="Washington DC", is_active=True),
        ]
        db.add_all(states)
        db.flush()
        
        # Seed federal agencies that we know we will encounter
        agencies = [
            Agency(
                code="HHS", 
                name="Department of Health and Human Services",
                level="federal",
                website_url="https://www.hhs.gov"
            ),
            Agency(
                code="ED", 
                name="Department of Education",
                level="federal",
                website_url="https://www.ed.gov"
            ),
            Agency(
                code="NSF", 
                name="National Science Foundation",
                level="federal",
                website_url="https://www.nsf.gov"
            ),
            Agency(
                code="USDA", 
                name="Department of Agriculture",
                level="federal",
                website_url="https://www.usda.gov"
            ),
            Agency(
                code="DOE", 
                name="Department of Energy",
                level="federal",
                website_url="https://www.energy.gov"
            ),
            Agency(
                code="EPA", 
                name="Environmental Protection Agency",
                level="federal",
                website_url="https://www.epa.gov"
            ),
            Agency(
                code="NIH", 
                name="National Institutes of Health",
                level="federal",
                website_url="https://www.nih.gov"
            ),
            Agency(
                code="NEA", 
                name="National Endowment for the Arts",
                level="federal",
                website_url="https://www.arts.gov"
            ),
            Agency(
                code="NEH", 
                name="National Endowment for the Humanities",
                level="federal",
                website_url="https://www.neh.gov"
            ),
            Agency(
                code="DOJ", 
                name="Department of Justice",
                level="federal",
                website_url="https://www.justice.gov"
            ),
        ]
        db.add_all(agencies)
        db.flush()
        
        # Seed the 12 opportunity categories from the project charter
        categories = [
            Category(
                name="Research and Innovation",
                slug="research-innovation",
                description="Scientific research, medical studies, technology development",
                display_order=1
            ),
            Category(
                name="Small Business and Entrepreneurship",
                slug="small-business",
                description="Startup funding, minority business support, rural businesses",
                display_order=2
            ),
            Category(
                name="Economic and Community Development",
                slug="economic-development",
                description="Infrastructure, job creation, business districts",
                display_order=3
            ),
            Category(
                name="Education and Academic Funding",
                slug="education",
                description="Scholarships, school programs, STEM education",
                display_order=4
            ),
            Category(
                name="Nonprofit and Social Services",
                slug="nonprofit",
                description="Community programs, food security, housing",
                display_order=5
            ),
            Category(
                name="Healthcare and Public Health",
                slug="healthcare",
                description="Hospitals, clinics, disease prevention, mental health",
                display_order=6
            ),
            Category(
                name="Technology and Digital Infrastructure",
                slug="technology",
                description="Broadband, cybersecurity, smart cities",
                display_order=7
            ),
            Category(
                name="Environment and Sustainability",
                slug="environment",
                description="Climate action, renewable energy, conservation",
                display_order=8
            ),
            Category(
                name="Arts, Culture and Humanities",
                slug="arts-culture",
                description="Museums, historic preservation, creative programs",
                display_order=9
            ),
            Category(
                name="Agriculture and Rural Development",
                slug="agriculture",
                description="Farm support, rural broadband, food systems",
                display_order=10
            ),
            Category(
                name="Workforce and Employment",
                slug="workforce",
                description="Job training, apprenticeships, career services",
                display_order=11
            ),
            Category(
                name="Disaster Relief and Emergency",
                slug="disaster-relief",
                description="Recovery funding, emergency preparedness",
                display_order=12
            ),
        ]
        db.add_all(categories)
        db.flush()
        
        # Seed applicant types
        # These cover both individual and organizational applicants
        applicant_types = [
            ApplicantType(
                code="IND",
                name="Individuals",
                description="Individual people applying for personal funding",
                is_individual=True
            ),
            ApplicantType(
                code="SMB",
                name="Small Businesses",
                description="Small business enterprises and startups",
                is_individual=False
            ),
            ApplicantType(
                code="NPO",
                name="Nonprofits",
                description="501(c)(3) organizations and NGOs",
                is_individual=False
            ),
            ApplicantType(
                code="EDU",
                name="Educational Institutions",
                description="Schools, colleges, universities",
                is_individual=False
            ),
            ApplicantType(
                code="GOV",
                name="Government Entities",
                description="State, local, and municipal governments",
                is_individual=False
            ),
            ApplicantType(
                code="TRB",
                name="Tribal Organizations",
                description="Native American tribal entities",
                is_individual=False
            ),
            ApplicantType(
                code="HLT",
                name="Healthcare Organizations",
                description="Hospitals, clinics, community health centers",
                is_individual=False
            ),
            ApplicantType(
                code="RES",
                name="Research Institutions",
                description="Research labs and institutions",
                is_individual=False
            ),
            ApplicantType(
                code="ART",
                name="Artists and Performers",
                description="Individual artists, writers, performers",
                is_individual=True
            ),
            ApplicantType(
                code="FRM",
                name="Farmers and Ranchers",
                description="Individual farmers and agricultural workers",
                is_individual=True
            ),
        ]
        db.add_all(applicant_types)
        
        db.commit()
        print("Reference data seeded successfully")
        print("Added 4 states, 10 agencies, 12 categories, 10 applicant types")
        
    except Exception as e:
        db.rollback()
        print(f"Error during seed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Initializing database tables")
    init_db()
    print()
    print("Seeding reference data")
    seed_initial_data()
    print()
    print("Database setup complete")

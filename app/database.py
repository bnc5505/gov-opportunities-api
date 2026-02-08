"""
Database configuration and session management
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from typing import Generator
import os
from dotenv import load_dotenv

load_dotenv()

# Database URL - will be configured for Azure SQL Database
# For local development, use SQLite
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./gov_grants.db"  # Default to SQLite for local dev
)

# For Azure SQL Server, the URL format will be:
# DATABASE_URL = "mssql+pyodbc://username:password@server.database.windows.net:1433/database?driver=ODBC+Driver+17+for+SQL+Server"

# For PostgreSQL on Azure, the URL format will be:
# DATABASE_URL = "postgresql://username:password@server.postgres.database.azure.com:5432/database?sslmode=require"

# Create engine
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},  # Needed for SQLite
        echo=True  # Set to False in production
    )
else:
    engine = create_engine(
        DATABASE_URL,
        echo=True,  # Set to False in production
        pool_pre_ping=True,  # Verify connections before using
        pool_size=10,
        max_overflow=20
    )

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function to get database session.
    Use this in FastAPI path operations.
    
    Example:
        @app.get("/grants/")
        def get_grants(db: Session = Depends(get_db)):
            return db.query(Grant).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Initialize database - create all tables.
    Import all models before calling this.
    """
    from models import Base
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")


def seed_initial_data():
    """
    Seed database with initial data (agencies, categories, applicant types).
    This should be run once after database initialization.
    """
    from models import Agency, Category, ApplicantType
    
    db = SessionLocal()
    
    try:
        # Check if data already exists
        if db.query(Agency).count() > 0:
            print("Database already seeded!")
            return
        
        # Seed Agencies
        agencies = [
            Agency(code="HHS", name="Department of Health and Human Services", 
                   website_url="https://www.hhs.gov"),
            Agency(code="ED", name="Department of Education", 
                   website_url="https://www.ed.gov"),
            Agency(code="NSF", name="National Science Foundation", 
                   website_url="https://www.nsf.gov"),
            Agency(code="USDA", name="Department of Agriculture", 
                   website_url="https://www.usda.gov"),
            Agency(code="DOE", name="Department of Energy", 
                   website_url="https://www.energy.gov"),
            Agency(code="EPA", name="Environmental Protection Agency", 
                   website_url="https://www.epa.gov"),
            Agency(code="NIH", name="National Institutes of Health", 
                   website_url="https://www.nih.gov"),
            Agency(code="NEA", name="National Endowment for the Arts", 
                   website_url="https://www.arts.gov"),
            Agency(code="NEH", name="National Endowment for the Humanities", 
                   website_url="https://www.neh.gov"),
            Agency(code="DOJ", name="Department of Justice", 
                   website_url="https://www.justice.gov"),
        ]
        db.add_all(agencies)
        
        # Seed Categories
        categories = [
            Category(name="Education & Training", slug="education-training", 
                    description="Grants for educational programs, scholarships, and training"),
            Category(name="Health & Medical", slug="health-medical", 
                    description="Health research, community health, disease prevention"),
            Category(name="Science & Technology", slug="science-technology", 
                    description="Research & development, innovation, STEM programs"),
            Category(name="Environment & Energy", slug="environment-energy", 
                    description="Conservation, clean energy, climate initiatives"),
            Category(name="Community Development", slug="community-development", 
                    description="Housing, infrastructure, economic development"),
            Category(name="Arts & Culture", slug="arts-culture", 
                    description="Museums, humanities, creative programs"),
            Category(name="Agriculture & Food", slug="agriculture-food", 
                    description="Farming, nutrition, rural development"),
            Category(name="Public Safety", slug="public-safety", 
                    description="Law enforcement, emergency management, firefighting"),
            Category(name="Social Services", slug="social-services", 
                    description="Youth programs, elderly care, homelessness"),
            Category(name="Infrastructure", slug="infrastructure", 
                    description="Transportation, utilities, public works"),
        ]
        db.add_all(categories)
        
        # Seed Applicant Types
        applicant_types = [
            ApplicantType(code="IND", name="Individuals", 
                         description="Individual persons"),
            ApplicantType(code="SMB", name="Small Businesses", 
                         description="Small business enterprises"),
            ApplicantType(code="NPO", name="Nonprofits/NGOs", 
                         description="Non-profit organizations"),
            ApplicantType(code="EDU", name="Educational Institutions", 
                         description="Schools, colleges, universities"),
            ApplicantType(code="GOV", name="State/Local Governments", 
                         description="State and local government entities"),
            ApplicantType(code="TRB", name="Tribal Organizations", 
                         description="Native American tribal organizations"),
            ApplicantType(code="HLT", name="Healthcare Organizations", 
                         description="Hospitals, clinics, health centers"),
            ApplicantType(code="RES", name="Research Institutions", 
                         description="Research organizations and labs"),
            ApplicantType(code="PUB", name="Public Housing Authorities", 
                         description="Public housing organizations"),
            ApplicantType(code="OTH", name="Other", 
                         description="Other eligible applicants"),
        ]
        db.add_all(applicant_types)
        
        db.commit()
        print("Database seeded successfully with initial data!")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("\nSeeding initial data...")
    seed_initial_data()
    print("\nDatabase setup complete!")

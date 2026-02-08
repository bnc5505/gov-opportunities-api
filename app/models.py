"""
Database models for Government Grants API
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, Enum, Table
from sqlalchemy.orm import relationship, declarative_base
import enum

Base = declarative_base()


# Association table for many-to-many relationship between grants and categories
grant_categories = Table(
    'grant_categories',
    Base.metadata,
    Column('grant_id', Integer, ForeignKey('grants.id'), primary_key=True),
    Column('category_id', Integer, ForeignKey('categories.id'), primary_key=True)
)

# Association table for many-to-many relationship between grants and eligible applicants
grant_eligible_applicants = Table(
    'grant_eligible_applicants',
    Base.metadata,
    Column('grant_id', Integer, ForeignKey('grants.id'), primary_key=True),
    Column('applicant_type_id', Integer, ForeignKey('applicant_types.id'), primary_key=True)
)


class GrantStatus(str, enum.Enum):
    """Enum for grant status"""
    FORECASTED = "forecasted"
    POSTED = "posted"
    OPEN = "open"
    CLOSING_SOON = "closing_soon"
    CLOSED = "closed"
    ARCHIVED = "archived"


class GrantType(str, enum.Enum):
    """Enum for grant types"""
    PROJECT = "project"
    FORMULA = "formula"
    BLOCK = "block"
    COMPETITIVE = "competitive"
    CONTINUATION = "continuation"
    COOPERATIVE_AGREEMENT = "cooperative_agreement"


class Grant(Base):
    """Main grant model"""
    __tablename__ = "grants"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Basic Information
    opportunity_number = Column(String(50), unique=True, index=True, nullable=False)
    title = Column(String(500), nullable=False, index=True)
    description = Column(Text, nullable=False)
    
    # Agency Information
    agency_id = Column(Integer, ForeignKey('agencies.id'), nullable=False)
    agency = relationship("Agency", back_populates="grants")
    
    # Grant Details
    grant_type = Column(Enum(GrantType), nullable=False)
    status = Column(Enum(GrantStatus), default=GrantStatus.POSTED, index=True)
    
    # Financial Information
    award_floor = Column(Float, nullable=True)  # Minimum grant amount
    award_ceiling = Column(Float, nullable=True)  # Maximum grant amount
    estimated_total_funding = Column(Float, nullable=True)
    expected_number_of_awards = Column(Integer, nullable=True)
    
    # Dates
    posted_date = Column(DateTime, nullable=True)
    close_date = Column(DateTime, nullable=True, index=True)
    archive_date = Column(DateTime, nullable=True)
    expected_award_date = Column(DateTime, nullable=True)
    
    # Additional Information
    cfda_number = Column(String(20), nullable=True)  # Catalog of Federal Domestic Assistance
    cost_sharing = Column(Boolean, default=False)
    eligibility_description = Column(Text, nullable=True)
    
    # Application Information
    application_url = Column(String(500), nullable=True)
    grantor_contact_email = Column(String(255), nullable=True)
    grantor_contact_name = Column(String(255), nullable=True)
    grantor_contact_phone = Column(String(50), nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_synced_at = Column(DateTime, nullable=True)
    
    # Relationships
    categories = relationship("Category", secondary=grant_categories, back_populates="grants")
    eligible_applicants = relationship("ApplicantType", secondary=grant_eligible_applicants, back_populates="grants")
    documents = relationship("GrantDocument", back_populates="grant", cascade="all, delete-orphan")
    saved_by_users = relationship("SavedGrant", back_populates="grant", cascade="all, delete-orphan")


class Agency(Base):
    """Federal agency model"""
    __tablename__ = "agencies"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    website_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    grants = relationship("Grant", back_populates="agency")


class Category(Base):
    """Grant category/subject area model"""
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Self-referential relationship for subcategories
    parent = relationship("Category", remote_side=[id], backref="subcategories")
    
    # Relationships
    grants = relationship("Grant", secondary=grant_categories, back_populates="categories")


class ApplicantType(Base):
    """Eligible applicant types model"""
    __tablename__ = "applicant_types"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(20), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    grants = relationship("Grant", secondary=grant_eligible_applicants, back_populates="eligible_applicants")


class GrantDocument(Base):
    """Grant-related documents model"""
    __tablename__ = "grant_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    grant_id = Column(Integer, ForeignKey('grants.id'), nullable=False)
    
    title = Column(String(255), nullable=False)
    document_type = Column(String(50), nullable=False)  # e.g., 'application', 'guidelines', 'FAQ'
    file_url = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)  # in bytes
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    grant = relationship("Grant", back_populates="documents")


class User(Base):
    """User model for authentication and personalization"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    organization = Column(String(255), nullable=True)
    
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, nullable=True)
    
    # Relationships
    saved_grants = relationship("SavedGrant", back_populates="user", cascade="all, delete-orphan")
    saved_searches = relationship("SavedSearch", back_populates="user", cascade="all, delete-orphan")


class SavedGrant(Base):
    """Saved/bookmarked grants by users"""
    __tablename__ = "saved_grants"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    grant_id = Column(Integer, ForeignKey('grants.id'), nullable=False)
    
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="saved_grants")
    grant = relationship("Grant", back_populates="saved_by_users")


class SavedSearch(Base):
    """Saved search queries for notifications"""
    __tablename__ = "saved_searches"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    
    name = Column(String(255), nullable=False)
    search_criteria = Column(Text, nullable=False)  # JSON string of search parameters
    notify_on_new_results = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_notified_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="saved_searches")

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,
    Float, Boolean, ForeignKey, Enum, Table, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.orm import declarative_base
import enum


Base = declarative_base()


opportunity_categories = Table(
    "opportunity_categories",
    Base.metadata,
    Column("opportunity_id", Integer, ForeignKey("opportunities.id"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id"), primary_key=True)
)

opportunity_applicant_types = Table(
    "opportunity_applicant_types",
    Base.metadata,
    Column("opportunity_id", Integer, ForeignKey("opportunities.id"), primary_key=True),
    Column("applicant_type_id", Integer, ForeignKey("applicant_types.id"), primary_key=True)
)


class OpportunityType(str, enum.Enum):
    GRANT = "grant"
    LOAN = "loan"
    TAX_CREDIT = "tax_credit"
    FELLOWSHIP = "fellowship"
    ACCELERATOR = "accelerator"
    WORKFORCE = "workforce"
    OTHER = "other"


class OpportunityStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    UNVERIFIED = "unverified"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_EDIT = "needs_edit"


class State(Base):
    __tablename__ = "states"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(5), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    opportunities = relationship("Opportunity", back_populates="state")
    agencies = relationship("Agency", back_populates="state")
    sources = relationship("Source", back_populates="state")


class Source(Base):
    """One row per website or API we scrape."""
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(String(1000), nullable=False)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)
    scraper_type = Column(String(50), nullable=False, default="scraper")  # api, scraper, rss, manual
    scrape_frequency_hours = Column(Integer, default=24)
    last_scraped_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    consecutive_failures = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    state = relationship("State", back_populates="sources")
    opportunities = relationship("Opportunity", back_populates="source")
    scrape_logs = relationship("ScrapeLog", back_populates="source", cascade="all, delete-orphan")


class Agency(Base):
    """Government agencies that offer funding. Federal agencies leave state_id null."""
    __tablename__ = "agencies"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    website_url = Column(String(1000), nullable=True)
    level = Column(String(20), nullable=False, default="federal")  # federal, state, local
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    state = relationship("State", back_populates="agencies")
    opportunities = relationship("Opportunity", back_populates="agency")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    display_order = Column(Integer, default=0)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    parent = relationship("Category", remote_side=[id], backref="subcategories")
    opportunities = relationship(
        "Opportunity",
        secondary=opportunity_categories,
        back_populates="categories"
    )


class ApplicantType(Base):
    """is_individual lets the frontend filter by individual vs. organization without a join."""
    __tablename__ = "applicant_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    code = Column(String(30), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    is_individual = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    opportunities = relationship(
        "Opportunity",
        secondary=opportunity_applicant_types,
        back_populates="eligible_applicants"
    )


class Opportunity(Base):
    """
    Central opportunities table.
    opportunity_key: SHA-256 dedup key — sha256(state_code|opportunity_url)
    """
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_key = Column(String(64), unique=True, nullable=True, index=True)
    title = Column(String(500), nullable=False, index=True)
    description = Column(Text, nullable=True)
    summary = Column(String(1000), nullable=True)
    opportunity_type = Column(
        Enum(OpportunityType),
        nullable=False,
        default=OpportunityType.GRANT
    )
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    agency_id = Column(Integer, ForeignKey("agencies.id"), nullable=True)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)
    eligibility_individual = Column(Boolean, default=False, index=True)
    eligibility_organization = Column(Boolean, default=False, index=True)
    eligibility_description = Column(Text, nullable=True)
    award_min = Column(Float, nullable=True)
    award_max = Column(Float, nullable=True)
    total_funding = Column(Float, nullable=True)
    deadline = Column(DateTime, nullable=True, index=True)
    rolling = Column(Boolean, nullable=True)  # no fixed deadline
    opportunity_url = Column(String(1000), nullable=True)   # grant info page
    application_url = Column(String(1000), nullable=True)   # direct apply link
    tags = Column(JSON, nullable=True)
    opportunity_gap_resources = Column(JSON, nullable=True)
    industry = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    contact_email = Column(String(255), nullable=True)
    status = Column(
        Enum(OpportunityStatus),
        default=OpportunityStatus.UNVERIFIED,
        index=True
    )
    data_quality_score = Column(Float, nullable=True)
    needs_review = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_synced_at = Column(DateTime, nullable=True)

    source = relationship("Source", back_populates="opportunities")
    agency = relationship("Agency", back_populates="opportunities")
    state = relationship("State", back_populates="opportunities")
    categories = relationship(
        "Category",
        secondary=opportunity_categories,
        back_populates="opportunities"
    )
    eligible_applicants = relationship(
        "ApplicantType",
        secondary=opportunity_applicant_types,
        back_populates="opportunities"
    )
    documents = relationship(
        "OpportunityDocument",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )
    saved_by_users = relationship(
        "SavedOpportunity",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )
    review_queue_entries = relationship(
        "ReviewQueue",
        back_populates="opportunity",
        cascade="all, delete-orphan"
    )


class OpportunityDocument(Base):
    """Documents attached to an opportunity (guidelines, FAQs). Stores URLs, not files."""
    __tablename__ = "opportunity_documents"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    title = Column(String(255), nullable=False)
    document_type = Column(String(50), nullable=False)
    file_url = Column(String(1000), nullable=False)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    opportunity = relationship("Opportunity", back_populates="documents")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    user_type = Column(String(20), nullable=False, default="individual")  # individual, organization
    organization_name = Column(String(255), nullable=True)
    organization_type = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    preferences = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, nullable=True)

    saved_opportunities = relationship(
        "SavedOpportunity",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    saved_searches = relationship(
        "SavedSearch",
        back_populates="user",
        cascade="all, delete-orphan"
    )

class ScrapedGrant(Base):
    """Staging table for raw scraped data. Enriched here before sync to opportunities."""
    __tablename__ = "scraped_grants"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    state = Column(String(5), nullable=True)
    status = Column(String(50), nullable=True)
    deadline = Column(String(20), nullable=True)
    rolling = Column(Boolean, nullable=True)
    is_annual = Column(Boolean, nullable=True)
    award_min = Column(Float, nullable=True)
    award_max = Column(Float, nullable=True)
    total_funding = Column(Float, nullable=True)
    award_text = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    summary = Column(String(1000), nullable=True)
    eligibility_notes = Column(Text, nullable=True)
    contact_email = Column(String(200), nullable=True)
    contact_name = Column(String(200), nullable=True)
    application_url = Column(String(1000), nullable=True)
    opportunity_url = Column(String(1000), nullable=True)
    tags = Column(Text, nullable=True)
    areas_of_focus = Column(Text, nullable=True)
    industry = Column(String(200), nullable=True)
    data_quality_score = Column(Float, nullable=True)
    needs_review = Column(Boolean, default=False)
    source_file = Column(String(500), nullable=True)
    content_hash = Column(String(64), nullable=True, index=True)
    combined_text = Column(Text, nullable=True)
    logo_url = Column(String(500), nullable=True)
    loaded_at = Column(DateTime, nullable=True)
    enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ScraperRun(Base):
    """Tracks scraper execution history for monitoring."""
    __tablename__ = "scraper_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(100), nullable=False, index=True)
    scraper_name = Column(String(200), nullable=False)
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    grants_found = Column(Integer, default=0)
    grants_new = Column(Integer, default=0)
    grants_updated = Column(Integer, default=0)
    grants_errors = Column(Integer, default=0)
    status = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

class SavedOpportunity(Base):
    """A user's bookmarked opportunity, with optional notes."""
    __tablename__ = "saved_opportunities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="saved_opportunities")
    opportunity = relationship("Opportunity", back_populates="saved_by_users")


class SavedSearch(Base):
    """A saved search that can trigger notifications when new matching grants appear."""
    __tablename__ = "saved_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    search_criteria = Column(JSON, nullable=False)
    notify_on_new_results = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_notified_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="saved_searches")


class ReviewQueue(Base):
    """Low-confidence pipeline records queued for human review."""
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    reason = Column(String(255), nullable=False)
    review_status = Column(
        Enum(ReviewStatus),
        default=ReviewStatus.PENDING,
        index=True
    )
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewer_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    opportunity = relationship("Opportunity", back_populates="review_queue_entries")


class ScrapeLog(Base):
    """One log entry per scraper run."""
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    records_found = Column(Integer, default=0)
    records_added = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)
    run_status = Column(String(20), nullable=False, default="SUCCESS")  # SUCCESS, FAILED
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = relationship("Source", back_populates="scrape_logs")

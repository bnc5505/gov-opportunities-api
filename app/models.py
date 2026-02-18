"""
Database models for GovGrants Hub.

This file defines the structure of every table in the database.
Each Python class here maps to one table. SQLAlchemy reads these
classes and creates the actual tables when we initialize the database.

We made significant changes from the original version because the
project scope expanded. The old models were built around federal grants
from Grants.gov. These new models support the full pipeline including
state scrapers, multiple opportunity types, pipeline monitoring,
and human review workflows.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,
    Float, Boolean, ForeignKey, Enum, Table, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.orm import declarative_base
import enum


Base = declarative_base()


# These two tables handle many-to-many relationships.
# An opportunity can belong to multiple categories and vice versa.
# We do not need full model classes for these, a simple table definition is enough.

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
    # The platform covers more than just grants.
    # This enum makes sure we always use one of these exact values
    # and never something like "Grant" or "GRANT" which would cause inconsistency.
    GRANT = "grant"
    LOAN = "loan"
    TAX_CREDIT = "tax_credit"
    FELLOWSHIP = "fellowship"
    ACCELERATOR = "accelerator"
    WORKFORCE = "workforce"
    OTHER = "other"


class OpportunityStatus(str, enum.Enum):
    # We simplified this compared to the old GrantStatus enum.
    # The old one had federal-specific statuses like FORECASTED and CLOSING_SOON
    # which do not apply to state-scraped data.
    # UNVERIFIED is the default for anything coming through the pipeline
    # before a human or automated check confirms the URL is still active.
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    UNVERIFIED = "unverified"


class ReviewStatus(str, enum.Enum):
    # Used in the ReviewQueue table to track where a record is
    # in the human review process.
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_EDIT = "needs_edit"


class State(Base):
    """
    Tracks the four pilot states: PA, NY, MD, DC.
    Having a proper states table means adding a new state later
    is just inserting a row, not changing any code.
    """
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
    """
    Every website or API we scrape gets a row in this table.
    This is essential for running the pipeline in production.
    When a scraper breaks, we need to know which source failed,
    when it last worked, and how many times in a row it has failed.
    Without this table we have no way to monitor or debug the pipeline.
    """
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(String(1000), nullable=False)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)

    # what kind of integration this is
    # options are: api, scraper, rss, manual
    scraper_type = Column(String(50), nullable=False, default="scraper")

    # how often we should run this scraper, measured in hours
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
    """
    Government agencies and institutions that offer funding.
    Federal agencies leave state_id as null.
    State and local agencies link to their state.
    """
    __tablename__ = "agencies"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    website_url = Column(String(1000), nullable=True)

    # federal, state, or local
    level = Column(String(20), nullable=False, default="federal")

    # only state and local agencies have a state_id
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    state = relationship("State", back_populates="agencies")
    opportunities = relationship("Opportunity", back_populates="agency")


class Category(Base):
    """
    The 12 opportunity categories from the project charter.
    The parent_id column supports subcategories if we need them later.
    display_order controls the order they appear on the frontend.
    """
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
    """
    The types of applicants who can apply for an opportunity.
    The is_individual flag lets us quickly query for opportunities
    open to individual applicants without joining to opportunities.
    """
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
    The central table of the entire platform.
    Every grant, loan, fellowship, tax credit, and accelerator
    ends up here after going through the scraping pipeline.

    Key design decisions explained:

    opportunity_number is nullable because state-scraped records will not have
    a federal-style opportunity number. The old model had this as required,
    which would have broken every state scraper we build.

    source_id tracks which scraper produced this record. This is how we
    debug pipeline issues and re-process records from a specific source.

    eligibility_individual and eligibility_organization are stored as simple
    booleans so the frontend can filter with a single checkbox without
    any complex joins.

    raw_source_data stores the original scraped content as JSON before we
    transformed it. If our parsing logic has a bug, we can fix the code
    and reprocess without scraping the website again.

    classification_confidence is the NLP model's confidence score for the
    category it assigned. Anything below a threshold goes to the review queue.

    application_url is the most important field for users. This is what
    the Apply button on the frontend links to.
    """
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)

    # opportunity_number is nullable on purpose, state sources do not have this
    opportunity_number = Column(String(100), unique=True, nullable=True, index=True)
    title = Column(String(500), nullable=False, index=True)
    description = Column(Text, nullable=True)
    summary = Column(String(1000), nullable=True)

    opportunity_type = Column(
        Enum(OpportunityType),
        nullable=False,
        default=OpportunityType.GRANT
    )

    # where this record came from
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    agency_id = Column(Integer, ForeignKey("agencies.id"), nullable=True)
    state_id = Column(Integer, ForeignKey("states.id"), nullable=True)

    # who can apply, these are the most important filter fields for users
    eligibility_individual = Column(Boolean, default=False, index=True)
    eligibility_organization = Column(Boolean, default=False, index=True)
    eligibility_description = Column(Text, nullable=True)

    # financial details, all optional because not every source provides these
    award_min = Column(Float, nullable=True)
    award_max = Column(Float, nullable=True)
    total_funding = Column(Float, nullable=True)
    expected_awards = Column(Integer, nullable=True)

    # dates
    posted_date = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True, index=True)
    expected_award_date = Column(DateTime, nullable=True)

    # this is what the Apply button on the frontend will link to
    application_url = Column(String(1000), nullable=True)

    # contact information from the source
    contact_name = Column(String(255), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)

    # only relevant for federal records from Grants.gov
    cfda_number = Column(String(20), nullable=True)

    # pipeline tracking fields
    status = Column(
        Enum(OpportunityStatus),
        default=OpportunityStatus.UNVERIFIED,
        index=True
    )

    # how complete is this record on a scale of 0 to 1
    data_quality_score = Column(Float, nullable=True)

    # how confident is the NLP classifier about the category it picked
    classification_confidence = Column(Float, nullable=True)

    # when was the application_url last confirmed as working
    last_verified_at = Column(DateTime, nullable=True)

    # the original scraped content before we transformed it
    raw_source_data = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_synced_at = Column(DateTime, nullable=True)

    # relationships
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
    """
    Documents attached to an opportunity such as application guidelines,
    FAQ documents, and program overviews. We store the URL to the file,
    not the file itself.
    """
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
    """
    User accounts for people who want to save opportunities
    and set up search alerts. Both individuals and organizational
    users share this table. user_type tells them apart.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)

    # individual or organization
    user_type = Column(String(20), nullable=False, default="individual")

    # only relevant for organizational users
    organization_name = Column(String(255), nullable=True)
    organization_type = Column(String(100), nullable=True)

    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    # stores things like preferred states and categories as JSON
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


class SavedOpportunity(Base):
    """
    When a user bookmarks an opportunity, we store it here.
    They can also add personal notes to remind themselves why they saved it.
    """
    __tablename__ = "saved_opportunities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="saved_opportunities")
    opportunity = relationship("Opportunity", back_populates="saved_by_users")


class SavedSearch(Base):
    """
    A search a user has saved so they can be notified when new
    matching opportunities appear. The search filters are stored as JSON
    so we can support any combination of filters without changing the schema.
    """
    __tablename__ = "saved_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)

    # stores the full filter state as a JSON object
    search_criteria = Column(JSON, nullable=False)

    notify_on_new_results = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_notified_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="saved_searches")


class ReviewQueue(Base):
    """
    This is the human-in-the-loop component of the pipeline.
    When the NLP classifier is not confident about a record, or when
    automated verification fails, the record lands here for a human to review.
    The reviewer can approve it, reject it, or flag it as needing an edit.
    """
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)

    # why did this end up in the queue
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
    """
    Every time a scraper runs, we write a log entry here.
    This gives us a history of how each source is performing,
    how many records it is finding, and whether it is erroring.
    Without this we have no visibility into the pipeline.
    """
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)

    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    records_found = Column(Integer, default=0)
    records_added = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)

    # SUCCESS or FAILED
    run_status = Column(String(20), nullable=False, default="SUCCESS")
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = relationship("Source", back_populates="scrape_logs")

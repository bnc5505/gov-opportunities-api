"""
Updated Database Models - Aligned with Runwei Platform and Sponsor Column Requirements

This schema reflects the exact field requirements provided by the sponsor.
Every column in the Opportunity model maps directly to a field the sponsor
specified in the extraction template.
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, Float, ForeignKey, Enum as SQLEnum, Table, UniqueConstraint
from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import enum

Base = declarative_base()


# Association tables for many-to-many relationships

opportunity_categories = Table(
    "opportunity_categories",
    Base.metadata,
    Column("opportunity_id", Integer, ForeignKey("opportunities.id"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id"), primary_key=True),
)

opportunity_applicant_types = Table(
    "opportunity_applicant_types",
    Base.metadata,
    Column("opportunity_id", Integer, ForeignKey("opportunities.id"), primary_key=True),
    Column("applicant_type_id", Integer, ForeignKey("applicant_types.id"), primary_key=True),
)


class OpportunityType(str, enum.Enum):
    # These match every category the sponsor listed in the Columns document.
    # We keep them as a proper enum so the UI can filter by type cleanly.
    GRANT                  = "grant"
    FELLOWSHIP             = "fellowship"
    PITCH_COMPETITION      = "pitch_competition"
    PAID_INTERN            = "paid_intern"
    PAID_FELLOW            = "paid_fellow"
    EVENT                  = "event"
    SCHOLARSHIP            = "scholarship"
    PRO_BONO_CONSULTANT    = "pro_bono_consultant"
    IN_KIND_SERVICE        = "in_kind_service"
    FORGIVABLE_LOAN        = "forgivable_loan"
    TAX_CREDIT             = "tax_credit"
    LEGISLATIVE_INITIATIVE = "legislative_initiative"
    ACCELERATOR            = "accelerator"
    COMPETITION            = "competition"
    CATALYST               = "catalyst"
    BOOTCAMP               = "bootcamp"
    INCUBATOR              = "incubator"
    STIPEND                = "stipend"
    MENTORSHIP             = "mentorship"
    ADVISOR                = "advisor"
    EXECUTIVE_COACH        = "executive_coach"
    BUSINESS_COACH         = "business_coach"
    VOLUNTEER              = "volunteer"
    PRO_BONO_LEGAL         = "pro_bono_legal"
    PAID_TRANSACTIONAL_LEGAL = "paid_transactional_legal"
    CONFERENCE             = "conference"
    WORKSHOP               = "workshop"
    CONVENING              = "convening"
    CONVENTION             = "convention"


class OpportunityStatus(str, enum.Enum):
    ACTIVE           = "active"
    EXPIRING_SOON    = "expiring_soon"    # less than 7 days to deadline
    ROLLING          = "rolling"          # no fixed deadline
    RECENTLY_CLOSED  = "recently_closed"
    ARCHIVED         = "archived"
    UNVERIFIED       = "unverified"       # needs human review before going live


class OpportunityCategory(str, enum.Enum):
    # These are the top-level tabs shown in the Runwei UI.
    PRIVATE    = "private_opportunities"
    GOVERNMENT = "government_grants"
    GLOBAL     = "global"
    FEATURED   = "featured"


# Core tables

class State(Base):
    """US states and territories we track opportunities for."""
    __tablename__ = "states"

    id   = Column(Integer, primary_key=True, index=True)
    code = Column(String(2),   unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)

    opportunities = relationship("Opportunity", back_populates="state")
    sources       = relationship("Source",      back_populates="state")


class Agency(Base):
    """
    Government agencies or organisations that administer grants.
    The sponsor calls this field Sponsor/ESO. We keep a separate agency
    table so the same funder can be linked to multiple opportunities
    without duplicating their details on every row.
    """
    __tablename__ = "agencies"

    id          = Column(Integer,     primary_key=True, index=True)
    code        = Column(String(50),  unique=True, nullable=False, index=True)
    name        = Column(String(255), nullable=False)
    level       = Column(String(20))   # federal, state, local
    state_id    = Column(Integer,     ForeignKey("states.id"), nullable=True)
    website_url = Column(String(500))
    logo_url    = Column(String(500))

    state         = relationship("State")
    opportunities = relationship("Opportunity", back_populates="agency")


class Source(Base):
    """The websites and APIs we scrape opportunities from."""
    __tablename__ = "sources"

    id                     = Column(Integer,     primary_key=True, index=True)
    name                   = Column(String(255), nullable=False)
    url                    = Column(String(500), nullable=False, unique=True, index=True)
    state_id               = Column(Integer,     ForeignKey("states.id"), nullable=True)
    scraper_type           = Column(String(50))
    scrape_frequency_hours = Column(Integer,     default=24)
    is_active              = Column(Boolean,     default=True)
    last_scraped_at        = Column(DateTime,    nullable=True)
    last_success_at        = Column(DateTime,    nullable=True)

    state        = relationship("State",    back_populates="sources")
    opportunities = relationship("Opportunity", back_populates="source")
    scrape_logs  = relationship("ScrapeLog",    back_populates="source")


class Opportunity(Base):
    """
    Main opportunities table.

    Every column here maps directly to a field the sponsor specified.
    The section comments below use the sponsor's exact field names so it
    is easy to cross-reference against the Columns document.
    """
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)

    # Title
    # Sponsor field: Title
    title = Column(String(500), nullable=False, index=True)

    # Sponsor/ESO and their website
    # Sponsor fields: Sponsor/ESO, Sponsor Website
    # sponsor_name duplicates agency.name for cases where we do not have a
    # full agency record yet, or where the sponsor is not a government body.
    sponsor_name    = Column(String(255), nullable=True)
    sponsor_website = Column(String(500), nullable=True)

    # Logo
    # Sponsor field: Logo Image Address URL
    logo_url = Column(String(500), nullable=True)

    # Geography
    # Sponsor fields: Global Opportunity, Location
    is_global  = Column(Boolean, default=False)  # Yes/No
    location   = Column(JSON,    nullable=True)   # list of regions or countries

    # URLs
    # Sponsor fields: Opportunity Link or URL, Direct Application Link
    opportunity_url = Column(String(500), nullable=True)  # the page describing the opportunity
    application_url = Column(String(500), nullable=False, default="")  # the apply button URL

    # Award amounts
    # Sponsor fields: Award Value (USD), Cash Award (USD)
    award_value = Column(String(100), nullable=True)  # display string e.g. "$25,000"
    award_min   = Column(Float,       nullable=True)
    award_max   = Column(Float,       nullable=True)
    cash_award  = Column(Float,       nullable=True)  # cash portion if separate from total award
    total_funding = Column(Float,     nullable=True)

    # Dates
    # Sponsor fields: Date Posted, Rolling, Deadline
    posted_date      = Column(DateTime, nullable=True)
    rolling          = Column(Boolean,  default=False)  # Yes/No
    deadline         = Column(DateTime, nullable=True, index=True)
    deadline_display = Column(String(100), nullable=True)  # e.g. "Jun 15, 2026"

    # Classification
    # Sponsor fields: Opportunity Category, Industry, Tags
    opportunity_type = Column(SQLEnum(OpportunityType),     default=OpportunityType.GRANT)
    category         = Column(SQLEnum(OpportunityCategory), default=OpportunityCategory.GOVERNMENT)
    status           = Column(SQLEnum(OpportunityStatus),   default=OpportunityStatus.UNVERIFIED, index=True)
    industry         = Column(String(100), nullable=True)
    tags             = Column(JSON, nullable=True)  # list of keyword strings

    # SDG Alignment
    # Sponsor field: SDG Alignment
    # Stored as a list of strings e.g. ["SDG 8: Decent Work and Economic Growth"]
    sdg_alignment = Column(JSON, nullable=True)

    # Opportunity Gap Resources
    # Sponsor field: Opportunity Gap Resources (Capital, Networks, Capacity Building)
    # Previously called areas_of_focus in the old schema.
    opportunity_gap_resources = Column(JSON, nullable=True)

    # Content
    # Sponsor fields: Summary, Description, Eligibility
    summary     = Column(String(500), nullable=True)  # 2 sentences max
    description = Column(Text,        nullable=True)  # 5-10 sentences
    eligibility_requirements = Column(JSON, nullable=True)  # list of bullet point strings

    # Eligibility flags used by the Runwei filter chips
    eligibility_individual   = Column(Boolean, default=False)
    eligibility_organization = Column(Boolean, default=True)

    # Global locations breakdown (separate from the boolean is_global)
    global_locations = Column(JSON, nullable=True)  # e.g. ["Europe", "West Africa"]

    # Contact
    # Sponsor fields: Contact Names, Contact Email
    contact_names = Column(String(500), nullable=True)  # comma-separated names
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(50),  nullable=True)

    # Additional checks
    # Sponsor fields: Fee Required, Cost to Participate, Equity Percentage, SAFE Note
    # These are flagged as disqualifying in some contexts so we store both
    # the boolean and the detail string for display in the review UI.
    fee_required          = Column(Boolean, default=False)
    fee_amount            = Column(String(100), nullable=True)
    cost_to_participate   = Column(Boolean, default=False)
    cost_amount           = Column(String(100), nullable=True)
    equity_percentage     = Column(Boolean, default=False)
    equity_details        = Column(String(255), nullable=True)
    safe_note             = Column(Boolean, default=False)
    safe_note_details     = Column(String(255), nullable=True)

    # Relationships to other tables
    source_id = Column(Integer, ForeignKey("sources.id"),  nullable=False)
    agency_id = Column(Integer, ForeignKey("agencies.id"), nullable=True)
    state_id  = Column(Integer, ForeignKey("states.id"),   nullable=True)

    # Data quality tracking
    data_quality_score    = Column(Float,   default=0.0)
    extraction_confidence = Column(Float,   default=0.0)
    needs_review          = Column(Boolean, default=True, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Raw extracted data kept for audit and manual verification
    raw_source_data = Column(JSON, nullable=True)

    source            = relationship("Source",        back_populates="opportunities")
    agency            = relationship("Agency",        back_populates="opportunities")
    state             = relationship("State",         back_populates="opportunities")
    categories        = relationship("Category",      secondary=opportunity_categories,    back_populates="opportunities")
    eligible_applicants = relationship("ApplicantType", secondary=opportunity_applicant_types, back_populates="opportunities")

    @property
    def agency_name(self):
        """Resolved agency name for OpportunityListItem serialization."""
        if self.agency:
            return self.agency.name
        return self.sponsor_name

    @property
    def state_code(self):
        """Resolved state code for OpportunityListItem serialization."""
        if self.state:
            return self.state.code
        return None


# Supporting tables

class Category(Base):
    """Grant categories used for filtering in the Runwei UI."""
    __tablename__ = "categories"

    id            = Column(Integer,      primary_key=True, index=True)
    name          = Column(String(100),  nullable=False)
    slug          = Column(String(100),  unique=True, nullable=False, index=True)
    description   = Column(Text,         nullable=True)
    display_order = Column(Integer,      default=0)
    parent_id     = Column(Integer,      ForeignKey("categories.id"), nullable=True)
    is_active     = Column(Boolean,      default=True)
    created_at    = Column(DateTime,     default=datetime.utcnow)

    opportunities = relationship("Opportunity", secondary=opportunity_categories, back_populates="categories")
    children      = relationship("Category", foreign_keys=[parent_id],
                                 backref=backref("parent", remote_side="[Category.id]"))


class ApplicantType(Base):
    """Types of eligible applicants, used for Runwei filter chips."""
    __tablename__ = "applicant_types"

    id            = Column(Integer,     primary_key=True, index=True)
    name          = Column(String(100), nullable=False)
    code          = Column(String(50),  unique=True, nullable=False, index=True)
    description   = Column(Text,        nullable=True)
    is_individual = Column(Boolean,     default=False)
    created_at    = Column(DateTime,    default=datetime.utcnow)

    opportunities = relationship("Opportunity", secondary=opportunity_applicant_types, back_populates="eligible_applicants")


class User(Base):
    """Platform users."""
    __tablename__ = "users"

    id                = Column(Integer,     primary_key=True, index=True)
    email             = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password   = Column(String(255), nullable=False)
    full_name         = Column(String(255), nullable=True)
    user_type         = Column(String(50),  nullable=True)
    organization_name = Column(String(255), nullable=True)
    organization_type = Column(String(100), nullable=True)
    is_active         = Column(Boolean,     default=True)
    is_admin          = Column(Boolean,     default=False)
    created_at        = Column(DateTime,    default=datetime.utcnow)
    last_login        = Column(DateTime,    nullable=True)

    saved_opportunities = relationship("SavedOpportunity", back_populates="user")
    saved_searches      = relationship("SavedSearch",      back_populates="user")


class SavedOpportunity(Base):
    """Opportunities bookmarked by users."""
    __tablename__ = "saved_opportunities"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"),        nullable=False)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    notes          = Column(Text,    nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", name="uq_user_opportunity"),)

    user        = relationship("User",        back_populates="saved_opportunities")
    opportunity = relationship("Opportunity")


class SavedSearch(Base):
    """Saved search queries with optional email notifications."""
    __tablename__ = "saved_searches"

    id                    = Column(Integer,     primary_key=True, index=True)
    user_id               = Column(Integer,     ForeignKey("users.id"), nullable=False)
    name                  = Column(String(255), nullable=False)
    search_criteria       = Column(JSON,        nullable=True)
    notify_on_new_results = Column(Boolean,     default=False)
    created_at            = Column(DateTime,    default=datetime.utcnow)
    updated_at            = Column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)
    last_notified_at      = Column(DateTime,    nullable=True)

    user = relationship("User", back_populates="saved_searches")


class ReviewQueue(Base):
    """
    Opportunities flagged for human review before going live.
    Records land here automatically when extraction_confidence is below 0.6
    or when any of the disqualifying flags are set (fee, equity, SAFE note).
    """
    __tablename__ = "review_queue"

    id             = Column(Integer,     primary_key=True, index=True)
    opportunity_id = Column(Integer,     ForeignKey("opportunities.id"), nullable=False)
    priority       = Column(Integer,     default=0)
    reason         = Column(String(255), nullable=True)
    assigned_to    = Column(String(100), nullable=True)
    reviewed       = Column(Boolean,     default=False, index=True)
    reviewed_at    = Column(DateTime,    nullable=True)
    reviewer_notes = Column(Text,        nullable=True)
    created_at     = Column(DateTime,    default=datetime.utcnow)

    opportunity    = relationship("Opportunity")


class ScrapeLog(Base):
    """One row per pipeline run. Used to track health and throughput."""
    __tablename__ = "scrape_logs"

    id             = Column(Integer,     primary_key=True, index=True)
    source_id      = Column(Integer,     ForeignKey("sources.id"), nullable=False)
    started_at     = Column(DateTime,    default=datetime.utcnow)
    completed_at   = Column(DateTime,    nullable=True)
    status         = Column(String(50),  nullable=True)
    grants_found   = Column(Integer,     default=0)
    grants_new     = Column(Integer,     default=0)
    grants_updated = Column(Integer,     default=0)
    error_message  = Column(Text,        nullable=True)

    source = relationship("Source", back_populates="scrape_logs")
"""
Pydantic Schemas - Aligned with Updated models.py and Sponsor Columns Document

These schemas are used for:
- API request validation (Create/Update)
- API response serialization (Response)
- Internal data transfer between pipeline and database layer

Every field here corresponds directly to a column in models.py.
"""

from pydantic import BaseModel, Field, HttpUrl, EmailStr, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ============================================================================
# ENUMS - Mirror the SQLAlchemy enums in models.py exactly
# ============================================================================

class OpportunityTypeEnum(str, Enum):
    GRANT                    = "grant"
    FELLOWSHIP               = "fellowship"
    PITCH_COMPETITION        = "pitch_competition"
    PAID_INTERN              = "paid_intern"
    PAID_FELLOW              = "paid_fellow"
    EVENT                    = "event"
    SCHOLARSHIP              = "scholarship"
    PRO_BONO_CONSULTANT      = "pro_bono_consultant"
    IN_KIND_SERVICE          = "in_kind_service"
    FORGIVABLE_LOAN          = "forgivable_loan"
    TAX_CREDIT               = "tax_credit"
    LEGISLATIVE_INITIATIVE   = "legislative_initiative"
    ACCELERATOR              = "accelerator"
    COMPETITION              = "competition"
    CATALYST                 = "catalyst"
    BOOTCAMP                 = "bootcamp"
    INCUBATOR                = "incubator"
    STIPEND                  = "stipend"
    MENTORSHIP               = "mentorship"
    ADVISOR                  = "advisor"
    EXECUTIVE_COACH          = "executive_coach"
    BUSINESS_COACH           = "business_coach"
    VOLUNTEER                = "volunteer"
    PRO_BONO_LEGAL           = "pro_bono_legal"
    PAID_TRANSACTIONAL_LEGAL = "paid_transactional_legal"
    CONFERENCE               = "conference"
    WORKSHOP                 = "workshop"
    CONVENING                = "convening"
    CONVENTION               = "convention"


class OpportunityStatusEnum(str, Enum):
    ACTIVE          = "active"
    EXPIRING_SOON   = "expiring_soon"
    ROLLING         = "rolling"
    RECENTLY_CLOSED = "recently_closed"
    ARCHIVED        = "archived"
    UNVERIFIED      = "unverified"


class OpportunityCategoryEnum(str, Enum):
    PRIVATE    = "private_opportunities"
    GOVERNMENT = "government_grants"
    GLOBAL     = "global"
    FEATURED   = "featured"


# ============================================================================
# STATE SCHEMAS
# ============================================================================

class StateBase(BaseModel):
    code: str = Field(..., max_length=2, description="Two-letter state/territory code")
    name: str = Field(..., max_length=100)


class StateCreate(StateBase):
    pass


class StateResponse(StateBase):
    id: int

    class Config:
        from_attributes = True


# ============================================================================
# AGENCY SCHEMAS
# ============================================================================

class AgencyBase(BaseModel):
    code:        str            = Field(..., max_length=50)
    name:        str            = Field(..., max_length=255)
    level:       Optional[str]  = Field(None, description="federal, state, or local")
    website_url: Optional[str]  = None
    logo_url:    Optional[str]  = None
    state_id:    Optional[int]  = None


class AgencyCreate(AgencyBase):
    pass


class AgencyResponse(AgencyBase):
    id: int

    class Config:
        from_attributes = True


# ============================================================================
# SOURCE SCHEMAS
# ============================================================================

class SourceBase(BaseModel):
    name:                   str           = Field(..., max_length=255)
    url:                    str           = Field(..., max_length=500)
    scraper_type:           Optional[str] = None
    scrape_frequency_hours: int           = 24
    is_active:              bool          = True
    state_id:               Optional[int] = None


class SourceCreate(SourceBase):
    pass


class SourceResponse(SourceBase):
    id:               int
    last_scraped_at:  Optional[datetime] = None
    last_success_at:  Optional[datetime] = None

    class Config:
        from_attributes = True


# ============================================================================
# CATEGORY SCHEMAS
# ============================================================================

class CategoryBase(BaseModel):
    name:          str           = Field(..., max_length=100)
    slug:          str           = Field(..., max_length=100)
    description:   Optional[str] = None
    display_order: int           = 0
    parent_id:     Optional[int] = None
    is_active:     bool          = True


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id:         int
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# APPLICANT TYPE SCHEMAS
# ============================================================================

class ApplicantTypeBase(BaseModel):
    name:          str           = Field(..., max_length=100)
    code:          str           = Field(..., max_length=50)
    description:   Optional[str] = None
    is_individual: bool          = False


class ApplicantTypeCreate(ApplicantTypeBase):
    pass


class ApplicantTypeResponse(ApplicantTypeBase):
    id:         int
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================================
# OPPORTUNITY SCHEMAS
# ============================================================================

class OpportunityBase(BaseModel):
    """
    Fields shared between Create, Update, and Response.
    Every field maps directly to a column in models.py Opportunity.
    Sponsor field names are noted in the comments.
    """

    # Sponsor field: Title
    title: str = Field(..., max_length=500)

    # Sponsor fields: Sponsor/ESO, Sponsor Website
    sponsor_name:    Optional[str] = Field(None, max_length=255)
    sponsor_website: Optional[str] = Field(None, max_length=500)

    # Sponsor field: Logo Image Address URL
    logo_url: Optional[str] = Field(None, max_length=500)

    # Sponsor fields: Global Opportunity (Yes/No), Location
    is_global: bool                  = False
    location:  Optional[List[str]]   = None

    # Sponsor fields: Opportunity Link or URL, Direct Application Link
    opportunity_url: Optional[str] = Field(None, max_length=500)
    application_url: str           = Field("", max_length=500)

    # Sponsor fields: Award Value (USD), Cash Award (USD)
    award_value:   Optional[str]   = Field(None, max_length=100,
                                           description="Display string e.g. '$25,000'")
    award_min:     Optional[float] = None
    award_max:     Optional[float] = None
    cash_award:    Optional[float] = Field(None,
                                           description="Cash portion of award if separate from total")
    total_funding: Optional[float] = None

    # Sponsor fields: Date Posted, Rolling (Yes/No), Deadline
    posted_date:      Optional[datetime] = None
    rolling:          bool               = False
    deadline:         Optional[datetime] = None
    deadline_display: Optional[str]      = Field(None, max_length=100,
                                                  description="Human-readable e.g. 'Jun 15, 2026'")

    # Sponsor fields: Opportunity Category, Industry, Tags
    opportunity_type: OpportunityTypeEnum     = OpportunityTypeEnum.GRANT
    category:         OpportunityCategoryEnum = OpportunityCategoryEnum.GOVERNMENT
    status:           OpportunityStatusEnum   = OpportunityStatusEnum.UNVERIFIED
    industry:         Optional[str]           = Field(None, max_length=100)
    tags:             Optional[List[str]]      = None

    # Sponsor field: SDG Alignment
    # List of strings e.g. ["SDG 8: Decent Work and Economic Growth"]
    sdg_alignment: Optional[List[str]] = None

    # Sponsor field: Opportunity Gap Resources (Capital, Networks, Capacity Building)
    opportunity_gap_resources: Optional[List[str]] = None

    # Sponsor fields: Summary (2 sentences max), Description (5-10 sentences), Eligibility
    summary:                  Optional[str]       = Field(None, max_length=500)
    description:              Optional[str]       = None
    eligibility_requirements: Optional[List[str]] = None

    # Eligibility flags used by Runwei filter chips
    eligibility_individual:   bool = False
    eligibility_organization: bool = True

    # Global locations detail list
    global_locations: Optional[List[str]] = None

    # Sponsor fields: Contact Names, Contact Email
    contact_names: Optional[str]  = Field(None, max_length=500)
    contact_email: Optional[str]  = Field(None, max_length=255)
    contact_phone: Optional[str]  = Field(None, max_length=50)

    # Sponsor additional checks: Fee Required
    fee_required: bool          = False
    fee_amount:   Optional[str] = Field(None, max_length=100)

    # Sponsor additional checks: Cost to Participate
    cost_to_participate: bool          = False
    cost_amount:         Optional[str] = Field(None, max_length=100)

    # Sponsor additional checks: Equity Percentage
    equity_percentage: bool          = False
    equity_details:    Optional[str] = Field(None, max_length=255)

    # Sponsor additional checks: SAFE Note
    safe_note:         bool          = False
    safe_note_details: Optional[str] = Field(None, max_length=255)

    # Relationship IDs
    source_id: int
    agency_id: Optional[int] = None
    state_id:  Optional[int] = None

    # Data quality
    data_quality_score:    float = 0.0
    extraction_confidence: float = 0.0
    needs_review:          bool  = True

    # Raw extraction data stored for audit trail
    raw_source_data: Optional[dict] = None


class OpportunityCreate(OpportunityBase):
    """Used when the pipeline or admin creates a new opportunity."""
    pass


class OpportunityUpdate(BaseModel):
    """
    All fields optional for PATCH-style updates.
    Only include the fields you want to change.
    """
    title:            Optional[str]                    = None
    sponsor_name:     Optional[str]                    = None
    sponsor_website:  Optional[str]                    = None
    logo_url:         Optional[str]                    = None
    is_global:        Optional[bool]                   = None
    location:         Optional[List[str]]               = None
    opportunity_url:  Optional[str]                    = None
    application_url:  Optional[str]                    = None
    award_value:      Optional[str]                    = None
    award_min:        Optional[float]                  = None
    award_max:        Optional[float]                  = None
    cash_award:       Optional[float]                  = None
    total_funding:    Optional[float]                  = None
    posted_date:      Optional[datetime]               = None
    rolling:          Optional[bool]                   = None
    deadline:         Optional[datetime]               = None
    deadline_display: Optional[str]                    = None
    opportunity_type: Optional[OpportunityTypeEnum]    = None
    category:         Optional[OpportunityCategoryEnum]= None
    status:           Optional[OpportunityStatusEnum]  = None
    industry:         Optional[str]                    = None
    tags:             Optional[List[str]]               = None
    sdg_alignment:             Optional[List[str]]     = None
    opportunity_gap_resources: Optional[List[str]]     = None
    summary:                   Optional[str]           = None
    description:               Optional[str]           = None
    eligibility_requirements:  Optional[List[str]]     = None
    eligibility_individual:    Optional[bool]          = None
    eligibility_organization:  Optional[bool]          = None
    global_locations:          Optional[List[str]]     = None
    contact_names:             Optional[str]           = None
    contact_email:             Optional[str]           = None
    contact_phone:             Optional[str]           = None
    fee_required:              Optional[bool]          = None
    fee_amount:                Optional[str]           = None
    cost_to_participate:       Optional[bool]          = None
    cost_amount:               Optional[str]           = None
    equity_percentage:         Optional[bool]          = None
    equity_details:            Optional[str]           = None
    safe_note:                 Optional[bool]          = None
    safe_note_details:         Optional[str]           = None
    agency_id:                 Optional[int]           = None
    state_id:                  Optional[int]           = None
    data_quality_score:        Optional[float]         = None
    extraction_confidence:     Optional[float]         = None
    needs_review:              Optional[bool]          = None
    raw_source_data:           Optional[dict]          = None


class OpportunityResponse(OpportunityBase):
    """Full response object returned by the API for a single opportunity."""
    id:         int
    created_at: datetime
    updated_at: Optional[datetime] = None

    # Nested objects resolved from foreign keys
    agency: Optional[AgencyResponse]   = None
    state:  Optional[StateResponse]    = None
    source: Optional[SourceResponse]   = None

    categories:          List[CategoryResponse]     = []
    eligible_applicants: List[ApplicantTypeResponse] = []

    class Config:
        from_attributes = True


class OpportunityListItem(BaseModel):
    """
    Lightweight schema for the Runwei search results grid.
    Only includes the fields shown on the grant card.
    Keeps the list endpoint fast by avoiding heavy joins.
    """
    id:               int
    title:            str
    sponsor_name:     Optional[str]                    = None
    logo_url:         Optional[str]                    = None
    award_value:      Optional[str]                    = None
    award_max:        Optional[float]                  = None
    deadline:         Optional[datetime]               = None
    deadline_display: Optional[str]                    = None
    rolling:          bool                             = False
    is_global:        bool                             = False
    opportunity_type: OpportunityTypeEnum
    category:         OpportunityCategoryEnum
    status:           OpportunityStatusEnum
    industry:         Optional[str]                    = None
    tags:             Optional[List[str]]               = None
    application_url:  str                              = ""

    # Disqualifying flags shown as warning chips in the Runwei card
    fee_required:      bool = False
    equity_percentage: bool = False
    safe_note:         bool = False

    # Resolved from joins
    agency_name: Optional[str] = None
    state_code:  Optional[str] = None

    class Config:
        from_attributes = True


# ============================================================================
# USER SCHEMAS
# ============================================================================

class UserBase(BaseModel):
    email:             str           = Field(..., max_length=255)
    full_name:         Optional[str] = Field(None, max_length=255)
    user_type:         Optional[str] = Field(None, max_length=50,
                                             description="individual or organization")
    organization_name: Optional[str] = Field(None, max_length=255)
    organization_type: Optional[str] = Field(None, max_length=100)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserResponse(UserBase):
    id:         int
    is_active:  bool
    is_admin:   bool
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============================================================================
# SAVED OPPORTUNITY SCHEMAS
# ============================================================================

class SavedOpportunityCreate(BaseModel):
    opportunity_id: int
    notes:          Optional[str] = None


class SavedOpportunityResponse(BaseModel):
    id:             int
    user_id:        int
    opportunity_id: int
    notes:          Optional[str] = None
    created_at:     datetime
    opportunity:    Optional[OpportunityListItem] = None

    class Config:
        from_attributes = True


# ============================================================================
# SAVED SEARCH SCHEMAS
# ============================================================================

class SavedSearchCreate(BaseModel):
    name:                   str           = Field(..., max_length=255)
    search_criteria:        Optional[dict] = None
    notify_on_new_results:  bool           = False


class SavedSearchResponse(SavedSearchCreate):
    id:              int
    user_id:         int
    created_at:      datetime
    updated_at:      datetime
    last_notified_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============================================================================
# REVIEW QUEUE SCHEMAS
# ============================================================================

class ReviewQueueResponse(BaseModel):
    id:             int
    opportunity_id: int
    priority:       int
    reason:         Optional[str] = None
    assigned_to:    Optional[str] = None
    reviewed:       bool
    reviewed_at:    Optional[datetime] = None
    reviewer_notes: Optional[str] = None
    created_at:     datetime
    opportunity:    Optional[OpportunityListItem] = None

    class Config:
        from_attributes = True


class ReviewQueueUpdate(BaseModel):
    """Used when an admin marks a review as complete."""
    reviewed:       bool
    reviewer_notes: Optional[str] = None
    assigned_to:    Optional[str] = None


# ============================================================================
# SCRAPE LOG SCHEMAS
# ============================================================================

class ScrapeLogResponse(BaseModel):
    id:             int
    source_id:      int
    started_at:     datetime
    completed_at:   Optional[datetime] = None
    status:         Optional[str]      = None
    grants_found:   int                = 0
    grants_new:     int                = 0
    grants_updated: int                = 0
    error_message:  Optional[str]      = None

    class Config:
        from_attributes = True


# ============================================================================
# SEARCH / FILTER SCHEMAS
# ============================================================================

class OpportunitySearchParams(BaseModel):
    """
    Query parameters for the Runwei search endpoint.
    All fields are optional so any combination of filters works.
    """
    q:                         Optional[str]                     = Field(None,
                                   description="Full text search across title, summary, description")
    opportunity_type:          Optional[OpportunityTypeEnum]     = None
    category:                  Optional[OpportunityCategoryEnum] = None
    status:                    Optional[OpportunityStatusEnum]   = None
    state_code:                Optional[str]                     = None
    is_global:                 Optional[bool]                    = None
    rolling:                   Optional[bool]                    = None
    industry:                  Optional[str]                     = None
    award_min:                 Optional[float]                   = None
    award_max:                 Optional[float]                   = None
    deadline_after:            Optional[datetime]                = None
    deadline_before:           Optional[datetime]                = None
    eligibility_individual:    Optional[bool]                    = None
    eligibility_organization:  Optional[bool]                    = None
    needs_review:              Optional[bool]                    = None

    # Disqualifying flag filters (e.g. show only non-dilutive opportunities)
    exclude_fee:               bool = False
    exclude_equity:            bool = False
    exclude_safe_note:         bool = False

    # Pagination
    page:     int = Field(1,   ge=1)
    per_page: int = Field(20,  ge=1, le=100)

    # Sorting
    sort_by:    str  = Field("deadline", description="Field name to sort by")
    sort_order: str  = Field("asc",      description="asc or desc")


class PaginatedOpportunityResponse(BaseModel):
    """Wraps a list response with pagination metadata."""
    total:    int
    page:     int
    per_page: int
    pages:    int
    items:    List[OpportunityListItem]
"""
Pydantic schemas for GovGrants Hub API.

Schemas are different from models. Models define the database tables.
Schemas define what data looks like when it travels through the API,
either coming in from a request or going out in a response.

The pattern we follow throughout this file is:
- Base: the shared fields between create and response
- Create: what we expect when someone creates a record
- Update: what we accept when someone edits a record, all fields optional
- Response: what we send back, includes id and timestamps

This pattern keeps things clean and makes it easy to control exactly
what data is exposed through the API versus what stays internal.
"""

from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# State schemas

class StateBase(BaseModel):
    code: str = Field(..., max_length=5)
    name: str = Field(..., max_length=100)
    is_active: bool = True


class StateCreate(StateBase):
    pass


class StateResponse(StateBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Source schemas
# Sources are the websites and APIs that the scrapers pull from.
# We expose limited fields here because most source management
# is done through the admin interface, not the public API.

class SourceBase(BaseModel):
    name: str = Field(..., max_length=255)
    url: str = Field(..., max_length=1000)
    state_id: Optional[int] = None
    scraper_type: str = Field(..., max_length=50)
    scrape_frequency_hours: int = 24
    is_active: bool = True


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    url: Optional[str] = Field(None, max_length=1000)
    scrape_frequency_hours: Optional[int] = None
    is_active: Optional[bool] = None


class SourceResponse(SourceBase):
    id: int
    last_scraped_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Agency schemas

class AgencyBase(BaseModel):
    code: str = Field(..., max_length=20)
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    website_url: Optional[str] = Field(None, max_length=1000)
    level: str = Field("federal", max_length=20)
    state_id: Optional[int] = None


class AgencyCreate(AgencyBase):
    pass


class AgencyUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    website_url: Optional[str] = Field(None, max_length=1000)
    level: Optional[str] = Field(None, max_length=20)


class AgencyResponse(AgencyBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Category schemas

class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    display_order: int = 0
    parent_id: Optional[int] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    display_order: Optional[int] = None
    parent_id: Optional[int] = None


class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Applicant type schemas

class ApplicantTypeBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=30)
    description: Optional[str] = None
    is_individual: bool = False


class ApplicantTypeCreate(ApplicantTypeBase):
    pass


class ApplicantTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    is_individual: Optional[bool] = None


class ApplicantTypeResponse(ApplicantTypeBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Opportunity document schemas

class OpportunityDocumentBase(BaseModel):
    title: str = Field(..., max_length=255)
    document_type: str = Field(..., max_length=50)
    file_url: str = Field(..., max_length=1000)
    file_size: Optional[int] = None


class OpportunityDocumentCreate(OpportunityDocumentBase):
    opportunity_id: int


class OpportunityDocumentResponse(OpportunityDocumentBase):
    id: int
    opportunity_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Opportunity schemas
# These are the most important schemas in the file.
# The Opportunity model is the core of the platform.

class OpportunityBase(BaseModel):
    # opportunity_number is optional because state sources do not have one
    opportunity_number: Optional[str] = Field(None, max_length=100)
    title: str = Field(..., max_length=500)
    description: Optional[str] = None
    summary: Optional[str] = Field(None, max_length=1000)

    opportunity_type: str = "grant"

    source_id: Optional[int] = None
    agency_id: Optional[int] = None
    state_id: Optional[int] = None

    # who can apply
    eligibility_individual: bool = False
    eligibility_organization: bool = False
    eligibility_description: Optional[str] = None

    # financial details
    award_min: Optional[float] = None
    award_max: Optional[float] = None
    total_funding: Optional[float] = None
    expected_awards: Optional[int] = None

    # dates
    posted_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    expected_award_date: Optional[datetime] = None

    # page describing the grant (vs the application form)
    opportunity_url: Optional[str] = Field(None, max_length=1000)

    # the link that the Apply button will point to
    application_url: Optional[str] = Field(None, max_length=1000)

    # sponsor branding
    logo_url: Optional[str] = Field(None, max_length=1000)
    sponsor_website: Optional[str] = Field(None, max_length=1000)

    # geographic scope
    is_global: bool = False
    locations: Optional[List[str]] = None  # list of regions or countries

    # financial structure
    cash_award: Optional[float] = None
    equity_percentage: Optional[float] = None
    safe_note: Optional[bool] = None

    # application cost
    fee_required: Optional[bool] = None
    fee_amount: Optional[float] = None
    cost_to_participate: Optional[float] = None

    # rolling applications (no fixed deadline)
    rolling: Optional[bool] = None

    # taxonomy
    tags: Optional[List[str]] = None
    sdg_alignment: Optional[List[str]] = None
    opportunity_gap_resources: Optional[List[str]] = None
    industry: Optional[str] = Field(None, max_length=255)

    # contact info
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)

    # federal records only
    cfda_number: Optional[str] = Field(None, max_length=20)


class OpportunityCreate(OpportunityBase):
    # when creating through the API we accept category and applicant type IDs
    category_ids: Optional[List[int]] = []
    applicant_type_ids: Optional[List[int]] = []


class OpportunityUpdate(BaseModel):
    # every field is optional here so we can do partial updates
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    summary: Optional[str] = Field(None, max_length=1000)
    opportunity_type: Optional[str] = None
    agency_id: Optional[int] = None
    state_id: Optional[int] = None
    eligibility_individual: Optional[bool] = None
    eligibility_organization: Optional[bool] = None
    eligibility_description: Optional[str] = None
    award_min: Optional[float] = None
    award_max: Optional[float] = None
    total_funding: Optional[float] = None
    posted_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    expected_award_date: Optional[datetime] = None
    opportunity_url: Optional[str] = Field(None, max_length=1000)
    application_url: Optional[str] = Field(None, max_length=1000)
    logo_url: Optional[str] = Field(None, max_length=1000)
    sponsor_website: Optional[str] = Field(None, max_length=1000)
    is_global: Optional[bool] = None
    locations: Optional[List[str]] = None
    cash_award: Optional[float] = None
    equity_percentage: Optional[float] = None
    safe_note: Optional[bool] = None
    fee_required: Optional[bool] = None
    fee_amount: Optional[float] = None
    cost_to_participate: Optional[float] = None
    rolling: Optional[bool] = None
    tags: Optional[List[str]] = None
    sdg_alignment: Optional[List[str]] = None
    opportunity_gap_resources: Optional[List[str]] = None
    industry: Optional[str] = Field(None, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = None
    category_ids: Optional[List[int]] = None
    applicant_type_ids: Optional[List[int]] = None


class OpportunityResponse(OpportunityBase):
    # this is what we send back when someone requests a full opportunity detail
    id: int
    status: str
    data_quality_score: Optional[float] = None
    classification_confidence: Optional[float] = None
    last_verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    last_synced_at: Optional[datetime] = None

    agency: Optional[AgencyResponse] = None
    state: Optional[StateResponse] = None
    categories: List[CategoryResponse] = []
    eligible_applicants: List[ApplicantTypeResponse] = []
    documents: List[OpportunityDocumentResponse] = []

    model_config = ConfigDict(from_attributes=True)


class OpportunityListItem(BaseModel):
    # a lighter version of OpportunityResponse used in search results
    # we do not need to send the full description in a list view
    id: int
    title: str
    opportunity_type: str
    status: str
    eligibility_individual: bool
    eligibility_organization: bool
    award_min: Optional[float] = None
    award_max: Optional[float] = None
    cash_award: Optional[float] = None
    deadline: Optional[datetime] = None
    rolling: Optional[bool] = None
    is_global: bool = False
    logo_url: Optional[str] = None
    opportunity_url: Optional[str] = None
    application_url: Optional[str] = None
    tags: Optional[List[str]] = None
    industry: Optional[str] = None
    agency: Optional[AgencyResponse] = None
    state: Optional[StateResponse] = None
    categories: List[CategoryResponse] = []

    model_config = ConfigDict(from_attributes=True)


# User schemas

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    user_type: str = Field("individual", max_length=20)
    organization_name: Optional[str] = None
    organization_type: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    organization_name: Optional[str] = None
    organization_type: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8)
    preferences: Optional[Dict[str, Any]] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


# Saved opportunity schemas

class SavedOpportunityBase(BaseModel):
    opportunity_id: int
    notes: Optional[str] = None


class SavedOpportunityCreate(SavedOpportunityBase):
    pass


class SavedOpportunityResponse(SavedOpportunityBase):
    id: int
    user_id: int
    created_at: datetime
    opportunity: OpportunityListItem

    model_config = ConfigDict(from_attributes=True)


# Saved search schemas
# search_criteria is stored as a dict here, not a JSON string.
# Storing it as a proper dict is cleaner and avoids double-serialization issues.

class SavedSearchBase(BaseModel):
    name: str = Field(..., max_length=255)
    search_criteria: Dict[str, Any]
    notify_on_new_results: bool = True


class SavedSearchCreate(SavedSearchBase):
    pass


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    search_criteria: Optional[Dict[str, Any]] = None
    notify_on_new_results: Optional[bool] = None


class SavedSearchResponse(SavedSearchBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    last_notified_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Review queue schemas
# These are used by admin users who review low-confidence pipeline records.

class ReviewQueueResponse(BaseModel):
    id: int
    opportunity_id: int
    reason: str
    review_status: str
    assigned_to: Optional[int] = None
    reviewer_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    opportunity: OpportunityListItem

    model_config = ConfigDict(from_attributes=True)


class ReviewQueueUpdate(BaseModel):
    review_status: str
    reviewer_notes: Optional[str] = None


# Scrape log schemas
# Read-only, these are only ever created by the pipeline, never through the API.

class ScrapeLogResponse(BaseModel):
    id: int
    source_id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    records_found: int
    records_added: int
    records_updated: int
    records_failed: int
    run_status: str
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Filter and search schemas
# This is what users send when they search for opportunities.

class OpportunityFilterParams(BaseModel):
    # text search across title and description
    search: Optional[str] = None

    # filter by type of opportunity
    opportunity_type: Optional[str] = None

    # filter by which state the opportunity is in
    state_id: Optional[int] = None
    state_code: Optional[str] = None

    # filter by category
    category_ids: Optional[List[int]] = None

    # filter by who can apply
    eligibility_individual: Optional[bool] = None
    eligibility_organization: Optional[bool] = None

    # filter by which agency is offering it
    agency_id: Optional[int] = None

    # filter by applicant type
    applicant_type_ids: Optional[List[int]] = None

    # filter by amount
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None

    # only show opportunities whose deadline is within this many days
    closing_within_days: Optional[int] = None

    # filter by status
    status: Optional[str] = None

    # pagination
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    # sorting
    sort_by: Optional[str] = Field("deadline", description="deadline, posted_date, award_max, title")
    sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")


class PaginatedOpportunityResponse(BaseModel):
    # wraps a list of opportunities with pagination metadata
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[OpportunityListItem]


# Auth token schemas

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: Optional[str] = None

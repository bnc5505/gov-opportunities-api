from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field, ConfigDict


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


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    display_order: int = 0
    parent_id: Optional[int] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicantTypeBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=30)
    description: Optional[str] = None
    is_individual: bool = False


class ApplicantTypeCreate(ApplicantTypeBase):
    pass


class ApplicantTypeResponse(ApplicantTypeBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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


class OpportunityBase(BaseModel):
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

    deadline: Optional[datetime] = None

    # rolling = no fixed deadline
    rolling: Optional[bool] = None

    # grant info page (vs the application form)
    opportunity_url: Optional[str] = Field(None, max_length=1000)

    # link the Apply button points to
    application_url: Optional[str] = Field(None, max_length=1000)

    tags: Optional[List[str]] = None
    opportunity_gap_resources: Optional[List[str]] = None
    industry: Optional[str] = Field(None, max_length=255)

    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)


class OpportunityCreate(OpportunityBase):
    category_ids: Optional[List[int]] = []
    applicant_type_ids: Optional[List[int]] = []


class OpportunityUpdate(BaseModel):
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
    deadline: Optional[datetime] = None
    rolling: Optional[bool] = None
    opportunity_url: Optional[str] = Field(None, max_length=1000)
    application_url: Optional[str] = Field(None, max_length=1000)
    tags: Optional[List[str]] = None
    opportunity_gap_resources: Optional[List[str]] = None
    industry: Optional[str] = Field(None, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = None
    category_ids: Optional[List[int]] = None
    applicant_type_ids: Optional[List[int]] = None


class OpportunityResponse(OpportunityBase):
    id: int
    status: str
    data_quality_score: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    last_synced_at: Optional[datetime] = None

    agency: Optional[AgencyResponse] = None
    state: Optional[StateResponse] = None
    categories: List[CategoryResponse] = []
    eligible_applicants: List[ApplicantTypeResponse] = []
    documents: List[OpportunityDocumentResponse] = []

    model_config = ConfigDict(from_attributes=True)


# Lighter version for search results — excludes full description
class OpportunityListItem(BaseModel):
    id: int
    title: str
    opportunity_type: str
    status: str
    eligibility_individual: bool
    eligibility_organization: bool
    award_min: Optional[float] = None
    award_max: Optional[float] = None
    deadline: Optional[datetime] = None
    rolling: Optional[bool] = None
    opportunity_url: Optional[str] = None
    application_url: Optional[str] = None
    tags: Optional[List[str]] = None
    industry: Optional[str] = None
    agency: Optional[AgencyResponse] = None
    state: Optional[StateResponse] = None
    categories: List[CategoryResponse] = []

    model_config = ConfigDict(from_attributes=True)


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


class PaginatedOpportunityResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[OpportunityListItem]


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: Optional[str] = None

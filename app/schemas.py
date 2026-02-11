"""
Pydantic schemas for API request/response validation
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============= Category Schemas =============
class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class Category(CategoryBase):
    id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============= Agency Schemas =============
class AgencyBase(BaseModel):
    code: str = Field(..., max_length=20)
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    website_url: Optional[str] = Field(None, max_length=500)


class AgencyCreate(AgencyBase):
    pass


class AgencyUpdate(BaseModel):
    code: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    website_url: Optional[str] = Field(None, max_length=500)


class Agency(AgencyBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============= Applicant Type Schemas =============
class ApplicantTypeBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=20)
    description: Optional[str] = None


class ApplicantTypeCreate(ApplicantTypeBase):
    pass


class ApplicantTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    code: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = None


class ApplicantType(ApplicantTypeBase):
    id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============= Grant Document Schemas =============
class GrantDocumentBase(BaseModel):
    title: str = Field(..., max_length=255)
    document_type: str = Field(..., max_length=50)
    file_url: str = Field(..., max_length=500)
    file_size: Optional[int] = None


class GrantDocumentCreate(GrantDocumentBase):
    grant_id: int


class GrantDocument(GrantDocumentBase):
    id: int
    grant_id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============= Grant Schemas =============
class GrantBase(BaseModel):
    opportunity_number: str = Field(..., max_length=50)
    title: str = Field(..., max_length=500)
    description: str
    agency_id: int
    grant_type: str
    status: str = "posted"
    award_floor: Optional[float] = None
    award_ceiling: Optional[float] = None
    estimated_total_funding: Optional[float] = None
    expected_number_of_awards: Optional[int] = None
    posted_date: Optional[datetime] = None
    close_date: Optional[datetime] = None
    archive_date: Optional[datetime] = None
    expected_award_date: Optional[datetime] = None
    cfda_number: Optional[str] = Field(None, max_length=20)
    cost_sharing: bool = False
    eligibility_description: Optional[str] = None
    application_url: Optional[str] = Field(None, max_length=500)
    grantor_contact_email: Optional[str] = Field(None, max_length=255)
    grantor_contact_name: Optional[str] = Field(None, max_length=255)
    grantor_contact_phone: Optional[str] = Field(None, max_length=50)


class GrantCreate(GrantBase):
    category_ids: Optional[List[int]] = []
    eligible_applicant_ids: Optional[List[int]] = []


class GrantUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    agency_id: Optional[int] = None
    grant_type: Optional[str] = None
    status: Optional[str] = None
    award_floor: Optional[float] = None
    award_ceiling: Optional[float] = None
    estimated_total_funding: Optional[float] = None
    expected_number_of_awards: Optional[int] = None
    posted_date: Optional[datetime] = None
    close_date: Optional[datetime] = None
    archive_date: Optional[datetime] = None
    expected_award_date: Optional[datetime] = None
    cfda_number: Optional[str] = Field(None, max_length=20)
    cost_sharing: Optional[bool] = None
    eligibility_description: Optional[str] = None
    application_url: Optional[str] = Field(None, max_length=500)
    grantor_contact_email: Optional[str] = Field(None, max_length=255)
    grantor_contact_name: Optional[str] = Field(None, max_length=255)
    grantor_contact_phone: Optional[str] = Field(None, max_length=50)
    category_ids: Optional[List[int]] = None
    eligible_applicant_ids: Optional[List[int]] = None


class Grant(GrantBase):
    id: int
    created_at: datetime
    updated_at: datetime
    last_synced_at: Optional[datetime] = None
    agency: Agency
    categories: List[Category] = []
    eligible_applicants: List[ApplicantType] = []
    documents: List[GrantDocument] = []
    
    model_config = ConfigDict(from_attributes=True)


class GrantListItem(BaseModel):
    """Simplified grant schema for list views"""
    id: int
    opportunity_number: str
    title: str
    agency: Agency
    grant_type: str
    status: str
    award_ceiling: Optional[float] = None
    close_date: Optional[datetime] = None
    categories: List[Category] = []
    
    model_config = ConfigDict(from_attributes=True)


# ============= User Schemas =============
class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    organization: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    organization: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8)


class User(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


# ============= Saved Grant Schemas =============
class SavedGrantBase(BaseModel):
    grant_id: int
    notes: Optional[str] = None


class SavedGrantCreate(SavedGrantBase):
    pass


class SavedGrant(SavedGrantBase):
    id: int
    user_id: int
    created_at: datetime
    grant: GrantListItem
    
    model_config = ConfigDict(from_attributes=True)


# ============= Saved Search Schemas =============
class SavedSearchBase(BaseModel):
    name: str = Field(..., max_length=255)
    search_criteria: str  # JSON string
    notify_on_new_results: bool = True


class SavedSearchCreate(SavedSearchBase):
    pass


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    search_criteria: Optional[str] = None
    notify_on_new_results: Optional[bool] = None


class SavedSearch(SavedSearchBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    last_notified_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============= Filter/Query Schemas =============
class GrantFilterParams(BaseModel):
    """Query parameters for filtering grants"""
    search: Optional[str] = Field(None, description="Search in title and description")
    agency_id: Optional[int] = None
    category_ids: Optional[List[int]] = None
    applicant_type_ids: Optional[List[int]] = None
    grant_type: Optional[str] = None
    status: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    closing_within_days: Optional[int] = None
    cost_sharing: Optional[bool] = None
    
    # Pagination
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    
    # Sorting
    sort_by: Optional[str] = Field("close_date", description="Field to sort by")
    sort_order: Optional[str] = Field("asc", pattern="^(asc|desc)$")


class PaginatedResponse(BaseModel):
    """Generic paginated response"""
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[GrantListItem]


# ============= Token Schema =============
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: Optional[str] = None

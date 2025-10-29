from ninja import Schema
from datetime import datetime, date
from typing import Optional, List


# User schemas
class UserSchema(Schema):
    id: int
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None


# Authentication schemas
class LoginRequest(Schema):
    username: str
    password: str


class TokenResponse(Schema):
    token: str
    user_id: int
    username: str
    iat: int
    exp: int


class ErrorResponse(Schema):
    error: str
    detail: str


# Profile schemas
class OsztalySimpleSchema(Schema):
    id: int
    tagozat: str
    kezdes_eve: int
    nev: str


class ProfileSchema(Schema):
    id: int
    user: UserSchema
    osztalyom: Optional[OsztalySimpleSchema] = None


# Osztaly schemas
class OsztalySchema(Schema):
    id: int
    tagozat: str
    kezdes_eve: int
    nev: str
    tanulok: List[UserSchema]
    osztalyfonokok: List[UserSchema]


# Mulasztas schemas
class MulasztasSchema(Schema):
    id: int
    datum: datetime  # DateField in model, but datetime works for both date and datetime
    ora: int
    tantargy: str
    tema: str
    tipus: str
    igazolt: bool
    igazolas_tipusa: Optional[str] = None
    rogzites_datuma: datetime  # DateField in model


# IgazolasTipus schemas
class IgazolasTipusSchema(Schema):
    id: int
    nev: str
    leiras: Optional[str] = None
    beleszamit: bool
    iskolaerdeku: bool


# Igazolas schemas
class IgazolasSchema(Schema):
    id: int
    profile: ProfileSchema
    mulasztasok: List[MulasztasSchema]
    eleje: datetime
    vege: datetime
    tipus: IgazolasTipusSchema
    megjegyzes: Optional[str] = None
    rogzites_datuma: date  # DateField in model (auto_now_add)
    megjegyzes_diak: Optional[str] = None
    diak: bool
    ftv: bool
    korrigalt: bool
    ftv_hianyzas_id: Optional[int] = None
    diak_extra_ido_elotte: Optional[int] = None
    diak_extra_ido_utana: Optional[int] = None
    imgDriveURL: Optional[str] = None
    bkk_verification: Optional[dict] = None
    allapot: str
    megjegyzes_tanar: Optional[str] = None
    kretaban_rogzitettem: bool


class IgazolasCreateRequest(Schema):
    eleje: datetime
    vege: datetime
    tipus: int  # IgazolasTipus ID
    megjegyzes_diak: Optional[str] = None
    diak: Optional[bool] = True
    korrigalt: Optional[bool] = False
    diak_extra_ido_elotte: Optional[int] = None
    diak_extra_ido_utana: Optional[int] = None
    imgDriveURL: Optional[str] = None
    bkk_verification: Optional[dict] = None


# Quick action schemas
class QuickActionRequest(Schema):
    action: str  # 'Elfogadva' or 'Elutasítva'


class BulkQuickActionRequest(Schema):
    action: str  # 'Elfogadva' or 'Elutasítva'
    ids: List[int]  # List of igazolas IDs


class QuickActionResponse(Schema):
    id: int
    allapot: str
    message: str


class BulkQuickActionResponse(Schema):
    updated_count: int
    failed_ids: List[int]
    message: str


# Teacher comment edit schemas
class TeacherCommentUpdateRequest(Schema):
    megjegyzes_tanar: Optional[str] = None


class TeacherCommentUpdateResponse(Schema):
    id: int
    megjegyzes_tanar: Optional[str] = None
    message: str


# Diakjaim schemas  
class IgazolasSimpleSchema(Schema):
    id: int
    eleje: datetime
    vege: datetime
    tipus: IgazolasTipusSchema
    allapot: str
    rogzites_datuma: date
    megjegyzes_diak: Optional[str] = None
    bkk_verification: Optional[dict] = None


class DiakjaSignleSchema(Schema):
    id: int
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    igazolasok: List[IgazolasSimpleSchema]


class DiakjaCreateRequest(Schema):
    last_name: str
    first_name: str
    email: str


class DiakjaCreateResponse(Schema):
    created_count: int
    failed_users: List[str]
    message: str


# Password Reset / OTP schemas
class ForgotPasswordRequest(Schema):
    username: str


class ForgotPasswordResponse(Schema):
    message: str
    email_sent: bool


class CheckOTPRequest(Schema):
    username: str
    otp_code: str


class CheckOTPResponse(Schema):
    message: str
    reset_token: str
    expires_in_minutes: int


class ChangePasswordOTPRequest(Schema):
    username: str
    reset_token: str
    new_password: str


class ChangePasswordOTPResponse(Schema):
    message: str
    success: bool

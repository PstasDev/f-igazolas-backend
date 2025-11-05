from ninja import Schema
from datetime import datetime
from datetime import date as DateType
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
    nem_fogadott_igazolas_tipusok: Optional[List['IgazolasTipusSchema']] = None


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
    nem_fogado_osztalyok: Optional[List[OsztalySimpleSchema]] = None


# Igazolas schemas
class IgazolasSchema(Schema):
    id: int
    profile: ProfileSchema
    mulasztasok: List[MulasztasSchema]
    eleje: datetime
    vege: datetime
    tipus: IgazolasTipusSchema
    megjegyzes: Optional[str] = None
    rogzites_datuma: DateType  # DateField in model (auto_now_add)
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
    rogzites_datuma: DateType
    megjegyzes_diak: Optional[str] = None
    bkk_verification: Optional[dict] = None


class DiakjaSignleSchema(Schema):
    id: int
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    last_action: Optional[datetime] = None
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


# Osztaly Igazolas Tipus Management schemas
class ToggleIgazolasTipusRequest(Schema):
    tipus_id: int
    enabled: bool  # True to enable (allow), False to disable (not accept)


class ToggleIgazolasTipusResponse(Schema):
    message: str
    success: bool
    tipus_id: int
    enabled: bool


# System Message schemas
class SystemMessageSchema(Schema):
    id: int
    title: str
    message: str
    severity: str
    messageType: str
    showFrom: datetime
    showTo: datetime
    created_at: datetime
    updated_at: datetime
    is_active: bool


# Tanítási Szünet schemas
class TanitasiSzunetSchema(Schema):
    id: int
    type: str
    name: Optional[str] = None
    from_date: DateType
    to_date: DateType
    description: Optional[str] = None


# Override schemas
class OverrideSchema(Schema):
    id: int
    date: DateType
    is_required: bool
    class_id: Optional[int] = None
    class_name: Optional[str] = None  # For convenience in response
    reason: Optional[str] = None


# Tanév Rendje combined schema
class TanevRendjeSchema(Schema):
    tanitasi_szunetek: List[TanitasiSzunetSchema]
    overrides: List[OverrideSchema]


# Create/Update request schemas
class TanitasiSzunetCreateRequest(Schema):
    type: str
    name: Optional[str] = None
    from_date: DateType
    to_date: DateType
    description: Optional[str] = None


class TanitasiSzunetUpdateRequest(Schema):
    type: Optional[str] = None
    name: Optional[str] = None
    from_date: Optional[DateType] = None
    to_date: Optional[DateType] = None
    description: Optional[str] = None


class OverrideCreateRequest(Schema):
    date: DateType
    is_required: bool
    class_id: Optional[int] = None
    reason: Optional[str] = None


class OverrideUpdateRequest(Schema):
    date: Optional[DateType] = None
    is_required: Optional[bool] = None
    class_id: Optional[int] = None
    reason: Optional[str] = None


# Superuser check schema
class SuperuserCheckResponse(Schema):
    is_superuser: bool
    username: str

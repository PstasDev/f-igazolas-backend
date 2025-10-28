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
    diak_extra_ido_elotte: Optional[int] = None
    diak_extra_ido_utana: Optional[int] = None
    imgDriveURL: Optional[str] = None
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

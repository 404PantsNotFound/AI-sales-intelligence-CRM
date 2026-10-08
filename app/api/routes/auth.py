from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.database.connection import get_db
from app.models import User
from app.schemas.auth import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services.auth_service import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])

# MANUAL EDIT
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user_route(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    user = register_user(db, payload)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login_user_route(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    return authenticate_user(db, payload)


@router.get("/me", response_model=UserResponse)
def get_current_user_route(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)

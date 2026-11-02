"""Auth endpoints: OAuth2 password login, refresh, current user (spec section 8)."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import RefreshRequest, TokenPair, UserOut
from app.core import audit
from app.core.config import settings
from app.core.errors import ApiError
from app.core.rate_limit import login_limiter
from app.core.rbac import get_current_user
from app.core.security import create_token, decode_token, verify_password
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(user: User) -> TokenPair:
    claims = dict(
        user_id=user.id,
        role=user.role.value,
        institution_id=user.institution_id,
        student_apaar_id=user.student_apaar_id,
    )
    return TokenPair(
        access_token=create_token(token_type="access", **claims),
        refresh_token=create_token(token_type="refresh", **claims),
        expires_in=settings.access_token_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenPair, summary="OAuth2 password login")
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenPair:
    """`username` is the user's email.

    Returns an access token (15 min) and a refresh token (7 days).
    Enforces in-memory rate limiting against brute-force attacks (spec Section 12).
    """
    client_ip = request.client.host if request.client else "unknown"
    email = form.username.strip().lower()
    rate_key = f"{client_ip}:{email}"

    # Rate limiting check (throws 429 if limit exceeded)
    login_limiter.check(rate_key)

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    ok = verify_password(form.password, user.password_hash if user else None)
    if not ok or user is None or not user.active:
        # Record failed attempt in rate limiter
        login_limiter.record_failure(rate_key)

        # Audited (FR-35) without the typed email (no PII in the log): the actor is the
        # matching account if one exists.
        reason = "unknown_account" if user is None else ("inactive" if ok else "bad_password")
        audit.record(
            db,
            "LOGIN_FAILED",
            actor=user,
            entity_type="user",
            entity_id=user.id if user else None,
            detail={"reason": reason},
        )
        db.commit()
        # Same message for unknown email, wrong password and inactive account.
        raise ApiError(
            401,
            "AUTH_INVALID_CREDENTIALS",
            "Incorrect email or password.",
            "errors.auth_invalid_credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Success clears rate limit counter
    login_limiter.record_success(rate_key)
    tokens = _issue_tokens(user)
    audit.record(db, "LOGIN", actor=user, entity_type="user", entity_id=user.id)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair, summary="Exchange a refresh token")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        user = db.get(User, int(payload["sub"]))
    except (jwt.InvalidTokenError, ValueError, KeyError):
        user = None
    if user is None or not user.active:
        raise ApiError(
            401,
            "AUTH_INVALID_REFRESH",
            "Your session has expired. Please sign in again.",
            "errors.auth_invalid_refresh",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_tokens(user)


@router.get("/me", response_model=UserOut, summary="Current user")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)

"""Role-based access control.

- `get_current_user`: resolves the bearer access token to an active user.
- `require_roles(...)`: dependency every protected route uses.
- `scope_to_institution(...)`: the ONE shared helper for row-level institution scoping
  (spec section 4 / 12). Every query over institution-owned rows must go through it.
"""

from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Select, false, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import decode_token
from app.db.models import Role, User
from app.db.session import get_db

AICTE_ROLES: frozenset[Role] = frozenset({Role.AICTE_ADMIN, Role.AICTE_REVIEWER})
INSTITUTION_ROLES: frozenset[Role] = frozenset({Role.INST_MAKER, Role.INST_CHECKER})
ALL_ROLES: frozenset[Role] = frozenset(Role)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/login", auto_error=False)

_WWW_AUTH = {"WWW-Authenticate": "Bearer"}


def _unauthorized(message: str = "Authentication required.") -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", message, "errors.auth_required", headers=_WWW_AUTH)


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    effective_token = token or request.query_params.get("token")
    if not effective_token:
        # Check X-API-Key for automated ERP integration (FR-39)
        api_key_header = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if api_key_header:
            from app.services.api_keys import verify_api_key

            key = verify_api_key(db, api_key_header)
            if not key:
                raise _unauthorized("Invalid or inactive API key.")
            user = db.scalar(
                select(User).where(
                    User.institution_id == key.institution_id,
                    User.role == Role.INST_MAKER,
                    User.active.is_(True),
                )
            )
            if user is None:
                user = User(
                    id=900000 + key.id,
                    email=f"erp-key-{key.id}@institution.internal",
                    role=Role.INST_MAKER,
                    institution_id=key.institution_id,
                    active=True,
                )
            return user
        raise _unauthorized()
    try:
        payload = decode_token(effective_token, expected_type="access")
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise _unauthorized(
            "Your session is invalid or has expired. Please sign in again."
        ) from None
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise _unauthorized("Your session is invalid or has expired. Please sign in again.")
    # The role in the DB is authoritative; the token's role claim is informational for the UI.
    return user


def require_roles(*roles: Role) -> Callable[..., User]:
    """Dependency factory: allow only the given roles.

    Usage: `user: User = Depends(require_roles(Role.AICTE_ADMIN))`
    """
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("require_roles() needs at least one role")

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise ApiError(
                403,
                "FORBIDDEN",
                "You do not have permission to perform this action.",
                "errors.forbidden",
                {"required_roles": sorted(r.value for r in allowed)},
            )
        return user

    _dependency.allowed_roles = allowed  # type: ignore[attr-defined]  # introspected by tests
    return _dependency


def scope_to_institution(stmt: Select, user: User, institution_column) -> Select:
    """Restrict `stmt` to rows the user may see.

    AICTE roles see everything; institution roles see only their own institution;
    any other role sees nothing (they must use their own dedicated, consent-gated endpoints).
    """
    if user.role in AICTE_ROLES:
        return stmt
    if user.role in INSTITUTION_ROLES and user.institution_id is not None:
        return stmt.where(institution_column == user.institution_id)
    return stmt.where(false())


def scope_cases(stmt: Select, user: User) -> Select:
    """Case visibility, enforced in the same layer as institution scoping (spec 12):
    AICTE roles see every case; institution roles see only `INSTITUTION`-visible cases of
    their own institution (never `AICTE_ONLY`); everyone else sees none."""
    from app.db.models import Case, CaseVisibility

    if user.role in AICTE_ROLES:
        return stmt
    if user.role in INSTITUTION_ROLES and user.institution_id is not None:
        return stmt.where(
            Case.visibility == CaseVisibility.INSTITUTION,
            Case.institution_id == user.institution_id,
        )
    return stmt.where(false())

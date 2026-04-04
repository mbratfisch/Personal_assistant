from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    email: str | None = None
    is_authenticated: bool = False


DEFAULT_USER_ID = "local-default"
PUBLIC_PATH_PREFIXES = (
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
    "/app",
    "/connect-google-calendar",
    "/integrations/google-calendar/auth/callback",
    "/integrations/telegram/webhook",
)
INVITATION_EXEMPT_PATH_PREFIXES = (
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
    "/app",
    "/connect-google-calendar",
    "/integrations/google-calendar/auth/callback",
    "/integrations/telegram/webhook",
    "/auth/me",
    "/auth/invitation",
    "/auth/invitation/",
)


def auth_mode() -> str:
    return (os.getenv("APP_AUTH_MODE") or "optional").strip().lower()


def is_public_path(path: str) -> bool:
    if path == "/":
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in PUBLIC_PATH_PREFIXES if prefix != "/")


def _project_id() -> str | None:
    return os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")


def invitation_mode() -> str:
    return (os.getenv("APP_INVITATION_MODE") or "off").strip().lower()


def invitation_required() -> bool:
    return invitation_mode() in {"required", "on", "enabled", "true", "1"}


def _safe_key(value: str | None) -> str:
    raw = (value or "default").strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)
    return safe or "default"


def _normalize_invitation_code(code: str) -> str:
    return "".join(ch for ch in (code or "").strip().upper() if ch.isalnum())


def _configured_invitation_codes() -> set[str]:
    raw = os.getenv("APP_INVITATION_CODES", "")
    return {_normalize_invitation_code(part) for part in raw.split(",") if _normalize_invitation_code(part)}


def _invitation_backend() -> str:
    return (os.getenv("APP_STORAGE_BACKEND") or "json").strip().lower()


@lru_cache(maxsize=1)
def _firestore_client():
    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Firestore dependencies are not installed. Add google-cloud-firestore to requirements.",
        ) from exc
    try:
        project_id = _project_id()
        if project_id:
            return firestore.Client(project=project_id)
        return firestore.Client()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not initialize Firestore client for invitations: {type(exc).__name__}: {exc}",
        ) from exc


def _invitation_file_path() -> Path:
    path = Path("data/invitations/invitation_registry.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _json_registry() -> dict[str, Any]:
    path = _invitation_file_path()
    if not path.exists():
        payload = {"allowed_users": {}, "codes": {}}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json_registry(payload: dict[str, Any]) -> None:
    _invitation_file_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _firestore_registry_refs():
    client = _firestore_client()
    collection = os.getenv("FIRESTORE_INVITATIONS_COLLECTION", "personal_assistant_invitations")
    return (
        client.collection(collection).document("allowed_users"),
        client.collection(collection).document("codes"),
    )


def _firestore_assistant_collection():
    client = _firestore_client()
    collection = os.getenv("FIRESTORE_COLLECTION", "personal_assistant")
    return client.collection(collection)


def _admin_uids() -> set[str]:
    raw = os.getenv("APP_ADMIN_UIDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _admin_emails() -> set[str]:
    raw = os.getenv("APP_ADMIN_EMAILS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_admin_user(auth: AuthContext) -> bool:
    if not auth.is_authenticated:
        return False
    if auth.user_id in _admin_uids():
        return True
    if auth.email and auth.email.lower() in _admin_emails():
        return True
    return False


def enforce_admin_access(auth: AuthContext) -> None:
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not is_admin_user(auth):
        raise HTTPException(status_code=403, detail="Admin access required.")


def _registry_state() -> tuple[dict[str, Any], dict[str, Any]]:
    if _invitation_backend() == "firestore":
        allowed_ref, codes_ref = _firestore_registry_refs()
        allowed_snapshot = allowed_ref.get()
        codes_snapshot = codes_ref.get()
        allowed_users = (allowed_snapshot.to_dict() or {}).get("users", {}) if allowed_snapshot.exists else {}
        codes = (codes_snapshot.to_dict() or {}).get("codes", {}) if codes_snapshot.exists else {}
        return allowed_users, codes
    payload = _json_registry()
    return payload.setdefault("allowed_users", {}), payload.setdefault("codes", {})


def _save_registry_state(allowed_users: dict[str, Any], codes: dict[str, Any]) -> None:
    if _invitation_backend() == "firestore":
        allowed_ref, codes_ref = _firestore_registry_refs()
        allowed_ref.set({"users": allowed_users}, merge=True)
        codes_ref.set({"codes": codes}, merge=True)
        return
    payload = {"allowed_users": allowed_users, "codes": codes}
    _save_json_registry(payload)


def _normalize_code_entry(code: str, entry: dict[str, Any] | None) -> dict[str, Any]:
    now_iso = datetime.utcnow().isoformat()
    payload = dict(entry or {})
    return {
        "code": _normalize_invitation_code(payload.get("code") or code),
        "active": bool(payload.get("active", True)),
        "created_at": payload.get("created_at", now_iso),
        "created_by_user_id": payload.get("created_by_user_id"),
        "created_by_email": payload.get("created_by_email"),
        "max_uses": int(payload.get("max_uses", 1) or 1),
        "redeemed_count": int(payload.get("redeemed_count", 0) or 0),
        "redeemed_by_users": list(payload.get("redeemed_by_users", [])),
        "user_id": payload.get("user_id"),
        "email": payload.get("email"),
        "invited_at": payload.get("invited_at"),
    }


def _all_invitation_codes_map() -> dict[str, dict[str, Any]]:
    _, stored_codes = _registry_state()
    normalized: dict[str, dict[str, Any]] = {}
    for code in _configured_invitation_codes():
        normalized[code] = _normalize_code_entry(code, stored_codes.get(code))
    for code, entry in stored_codes.items():
        normalized[_normalize_invitation_code(code)] = _normalize_code_entry(code, entry)
    return normalized


def list_admin_invitation_codes() -> list[dict[str, Any]]:
    codes = list(_all_invitation_codes_map().values())
    return sorted(codes, key=lambda item: item.get("created_at") or "", reverse=True)


def create_admin_invitation_code(
    created_by_user_id: str,
    created_by_email: str | None,
    code: str | None,
    max_uses: int = 1,
    active: bool = True,
) -> dict[str, Any]:
    normalized = _normalize_invitation_code(code or secrets.token_hex(4))
    if not normalized:
        raise HTTPException(status_code=400, detail="Invitation code is required.")
    allowed_users, codes = _registry_state()
    existing = codes.get(normalized)
    if existing:
        raise HTTPException(status_code=409, detail="Invitation code already exists.")
    entry = _normalize_code_entry(
        normalized,
        {
            "code": normalized,
            "active": active,
            "created_at": datetime.utcnow().isoformat(),
            "created_by_user_id": created_by_user_id,
            "created_by_email": created_by_email,
            "max_uses": max(1, max_uses),
            "redeemed_count": 0,
            "redeemed_by_users": [],
        },
    )
    codes[normalized] = entry
    _save_registry_state(allowed_users, codes)
    return entry


def update_admin_invitation_code(code: str, updates: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_invitation_code(code)
    allowed_users, codes = _registry_state()
    existing = _all_invitation_codes_map().get(normalized)
    if existing is None:
        raise HTTPException(status_code=404, detail="Invitation code was not found.")
    if "active" in updates and updates["active"] is not None:
        existing["active"] = bool(updates["active"])
    if "max_uses" in updates and updates["max_uses"] is not None:
        existing["max_uses"] = max(1, int(updates["max_uses"]))
    codes[normalized] = existing
    _save_registry_state(allowed_users, codes)
    return existing


def delete_admin_invitation_code(code: str) -> None:
    normalized = _normalize_invitation_code(code)
    allowed_users, codes = _registry_state()
    if normalized not in _all_invitation_codes_map():
        raise HTTPException(status_code=404, detail="Invitation code was not found.")
    codes.pop(normalized, None)
    _save_registry_state(allowed_users, codes)


def list_admin_users() -> list[dict[str, Any]]:
    allowed_users, _ = _registry_state()
    by_user: dict[str, dict[str, Any]] = {}

    for payload in allowed_users.values():
        user_id = payload.get("user_id")
        if not user_id:
            continue
        by_user[user_id] = {
            "user_id": user_id,
            "email": payload.get("email"),
            "full_name": None,
            "invitation_redeemed": True,
            "redeemed_code": payload.get("code"),
            "invited_at": payload.get("invited_at"),
            "google_calendar_connected": False,
        }

    if _invitation_backend() == "firestore":
        try:
            docs = _firestore_assistant_collection().stream()
            for doc in docs:
                payload = doc.to_dict() or {}
                settings = payload.get("settings", {}) or {}
                user_id = doc.id
                entry = by_user.setdefault(
                    user_id,
                    {
                        "user_id": user_id,
                        "email": settings.get("gmail_address"),
                        "full_name": settings.get("full_name"),
                        "invitation_redeemed": False,
                        "redeemed_code": None,
                        "invited_at": None,
                        "google_calendar_connected": False,
                    },
                )
                entry["email"] = entry.get("email") or settings.get("gmail_address")
                entry["full_name"] = settings.get("full_name") or entry.get("full_name")
                entry["google_calendar_connected"] = bool(settings.get("google_calendar_connected_profile_key"))
        except Exception:
            pass
    else:
        users_root = Path("data/users")
        if users_root.exists():
            for child in users_root.iterdir():
                db_path = child / "assistant_db.json"
                if not db_path.exists():
                    continue
                try:
                    payload = json.loads(db_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                settings = payload.get("settings", {}) or {}
                user_id = child.name
                entry = by_user.setdefault(
                    user_id,
                    {
                        "user_id": user_id,
                        "email": settings.get("gmail_address"),
                        "full_name": settings.get("full_name"),
                        "invitation_redeemed": False,
                        "redeemed_code": None,
                        "invited_at": None,
                        "google_calendar_connected": False,
                    },
                )
                entry["email"] = entry.get("email") or settings.get("gmail_address")
                entry["full_name"] = settings.get("full_name") or entry.get("full_name")
                entry["google_calendar_connected"] = bool(settings.get("google_calendar_connected_profile_key"))

    return sorted(by_user.values(), key=lambda item: ((item.get("full_name") or item.get("email") or item["user_id"]).lower()))


def get_admin_overview() -> dict[str, int]:
    users = list_admin_users()
    codes = list_admin_invitation_codes()
    return {
        "total_users": len(users),
        "invited_users": sum(1 for user in users if user.get("invitation_redeemed")),
        "google_calendar_connected_users": sum(1 for user in users if user.get("google_calendar_connected")),
        "total_invitation_codes": len(codes),
        "active_invitation_codes": sum(1 for code in codes if code.get("active")),
        "redeemed_invitation_codes": sum(1 for code in codes if int(code.get("redeemed_count", 0) or 0) > 0),
    }


def list_admin_assistant_diagnostics(limit: int = 25) -> list[dict[str, Any]]:
    user_index = {
        item["user_id"]: item
        for item in list_admin_users()
    }
    diagnostics: list[dict[str, Any]] = []

    if _invitation_backend() == "firestore":
        try:
            docs = _firestore_assistant_collection().stream()
            for doc in docs:
                payload = doc.to_dict() or {}
                user_meta = user_index.get(doc.id, {})
                for diagnostic in payload.get("assistant_diagnostics", []) or []:
                    diagnostics.append(
                        {
                            "user_id": doc.id,
                            "email": user_meta.get("email"),
                            "full_name": user_meta.get("full_name"),
                            **diagnostic,
                        }
                    )
        except Exception:
            return []
    else:
        users_root = Path("data/users")
        if users_root.exists():
            for child in users_root.iterdir():
                db_path = child / "assistant_db.json"
                if not db_path.exists():
                    continue
                try:
                    payload = json.loads(db_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                user_meta = user_index.get(child.name, {})
                for diagnostic in payload.get("assistant_diagnostics", []) or []:
                    diagnostics.append(
                        {
                            "user_id": child.name,
                            "email": user_meta.get("email"),
                            "full_name": user_meta.get("full_name"),
                            **diagnostic,
                        }
                    )

    diagnostics.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
    return diagnostics[: max(1, limit)]


def get_invitation_status(user_id: str) -> dict[str, Any]:
    if not invitation_required():
        return {
            "invitation_required": False,
            "invitation_redeemed": True,
            "message": "Invitation codes are not required.",
            "invited_at": None,
            "redeemed_code": None,
        }

    if _invitation_backend() == "firestore":
        allowed_ref, _ = _firestore_registry_refs()
        snapshot = allowed_ref.get()
        allowed_users = (snapshot.to_dict() or {}).get("users", {}) if snapshot.exists else {}
    else:
        allowed_users = _json_registry().get("allowed_users", {})

    entry = allowed_users.get(_safe_key(user_id))
    if entry:
        return {
            "invitation_required": True,
            "invitation_redeemed": True,
            "message": "Invitation already redeemed for this account.",
            "invited_at": entry.get("invited_at"),
            "redeemed_code": entry.get("code"),
        }
    return {
        "invitation_required": True,
        "invitation_redeemed": False,
        "message": "An invitation code is required before this account can access the app.",
        "invited_at": None,
        "redeemed_code": None,
    }


def redeem_invitation_code(user_id: str, email: str | None, code: str) -> dict[str, Any]:
    if not invitation_required():
        return get_invitation_status(user_id)

    normalized = _normalize_invitation_code(code)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invitation code is required.")
    code_registry = _all_invitation_codes_map()
    configured = _configured_invitation_codes()
    code_entry = code_registry.get(normalized)
    if normalized not in configured and code_entry is None:
        raise HTTPException(status_code=403, detail="Invitation code is invalid.")
    if code_entry is not None and not code_entry.get("active", True):
        raise HTTPException(status_code=403, detail="Invitation code is inactive.")

    user_key = _safe_key(user_id)

    if _invitation_backend() == "firestore":
        from google.cloud import firestore

        allowed_ref, codes_ref = _firestore_registry_refs()
        transaction = _firestore_client().transaction()

        @firestore.transactional
        def _redeem(txn):
            allowed_snapshot = allowed_ref.get(transaction=txn)
            codes_snapshot = codes_ref.get(transaction=txn)
            allowed_users = (allowed_snapshot.to_dict() or {}).get("users", {}) if allowed_snapshot.exists else {}
            codes = (codes_snapshot.to_dict() or {}).get("codes", {}) if codes_snapshot.exists else {}

            existing_user = allowed_users.get(user_key)
            if existing_user:
                return existing_user

            code_entry = _normalize_code_entry(normalized, codes.get(normalized))
            if not code_entry.get("active", True):
                raise HTTPException(status_code=403, detail="Invitation code is inactive.")
            already_used = code_entry.get("redeemed_count", 0) >= code_entry.get("max_uses", 1)
            same_user = user_id in code_entry.get("redeemed_by_users", []) or code_entry.get("user_id") == user_id
            if already_used and not same_user:
                raise HTTPException(status_code=403, detail="Invitation code has already been used.")

            invited_at = firestore.SERVER_TIMESTAMP
            allowed_users[user_key] = {
                "user_id": user_id,
                "email": email,
                "code": normalized,
                "invited_at": invited_at,
            }
            code_entry["user_id"] = user_id
            code_entry["email"] = email
            code_entry["code"] = normalized
            code_entry["invited_at"] = invited_at
            redeemed_by_users = list(code_entry.get("redeemed_by_users", []))
            if user_id not in redeemed_by_users:
                redeemed_by_users.append(user_id)
            code_entry["redeemed_by_users"] = redeemed_by_users
            code_entry["redeemed_count"] = len(redeemed_by_users)
            codes[normalized] = code_entry
            txn.set(allowed_ref, {"users": allowed_users}, merge=True)
            txn.set(codes_ref, {"codes": codes}, merge=True)
            return {"user_id": user_id, "email": email, "code": normalized, "invited_at": None}

        entry = _redeem(transaction)
    else:
        payload = _json_registry()
        allowed_users = payload.setdefault("allowed_users", {})
        codes = payload.setdefault("codes", {})

        existing_user = allowed_users.get(user_key)
        if existing_user:
            entry = existing_user
        else:
            code_entry = _normalize_code_entry(normalized, codes.get(normalized))
            if not code_entry.get("active", True):
                raise HTTPException(status_code=403, detail="Invitation code is inactive.")
            already_used = code_entry.get("redeemed_count", 0) >= code_entry.get("max_uses", 1)
            same_user = user_id in code_entry.get("redeemed_by_users", []) or code_entry.get("user_id") == user_id
            if already_used and not same_user:
                raise HTTPException(status_code=403, detail="Invitation code has already been used.")
            entry = {
                "user_id": user_id,
                "email": email,
                "code": normalized,
                "invited_at": datetime.utcnow().isoformat(),
            }
            allowed_users[user_key] = entry
            redeemed_by_users = list(code_entry.get("redeemed_by_users", []))
            if user_id not in redeemed_by_users:
                redeemed_by_users.append(user_id)
            code_entry.update(
                {
                    "user_id": user_id,
                    "email": email,
                    "code": normalized,
                    "invited_at": entry["invited_at"],
                    "redeemed_by_users": redeemed_by_users,
                    "redeemed_count": len(redeemed_by_users),
                }
            )
            codes[normalized] = code_entry
            _save_json_registry(payload)

    return {
        "invitation_required": True,
        "invitation_redeemed": True,
        "message": "Invitation code accepted. This account now has access.",
        "invited_at": entry.get("invited_at"),
        "redeemed_code": entry.get("code"),
    }


def enforce_invitation_access(request: Request, auth: AuthContext) -> None:
    if not invitation_required() or not auth.is_authenticated:
        return
    if any(request.url.path == prefix or request.url.path.startswith(f"{prefix}/") for prefix in INVITATION_EXEMPT_PATH_PREFIXES if prefix != "/"):
        return
    if request.url.path == "/":
        return
    status = get_invitation_status(auth.user_id)
    if status["invitation_redeemed"]:
        return
    raise HTTPException(status_code=403, detail="Invitation code required for this account.")


@lru_cache(maxsize=1)
def _firebase_auth_module():
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Firebase Admin SDK is not installed. Add firebase-admin to requirements.",
        ) from exc

    if not firebase_admin._apps:
        kwargs = {}
        project_id = _project_id()
        if project_id:
            kwargs["options"] = {"projectId": project_id}
        firebase_admin.initialize_app(**kwargs)
    return firebase_auth


def verify_bearer_token(authorization: str | None) -> AuthContext | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid Authorization header.")
    firebase_auth = _firebase_auth_module()
    try:
        decoded = firebase_auth.verify_id_token(token.strip())
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid auth token: {type(exc).__name__}: {exc}") from exc
    return AuthContext(
        user_id=decoded["uid"],
        email=decoded.get("email"),
        is_authenticated=True,
    )


def resolve_auth_context(request: Request) -> AuthContext:
    if request.method.upper() == "OPTIONS":
        return AuthContext(user_id=DEFAULT_USER_ID, email=None, is_authenticated=False)
    mode = auth_mode()
    path = request.url.path
    authorization = request.headers.get("Authorization")
    token_context = verify_bearer_token(authorization) if authorization else None
    if token_context is not None:
        return token_context
    if mode == "required" and not is_public_path(path):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return AuthContext(user_id=DEFAULT_USER_ID, email=None, is_authenticated=False)

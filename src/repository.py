from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from fastapi import HTTPException

from src.assistant_models import DatabaseModel


class AssistantRepository(Protocol):
    def load(self) -> DatabaseModel: ...

    def save(self, data: DatabaseModel) -> DatabaseModel: ...


class JsonRepository:
    def __init__(self, db_path: str = "data/assistant_db.json") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.save(DatabaseModel())

    def load(self) -> DatabaseModel:
        raw = self.db_path.read_text(encoding="utf-8")
        return DatabaseModel.model_validate_json(raw)

    def save(self, data: DatabaseModel) -> DatabaseModel:
        self.db_path.write_text(
            json.dumps(data.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return data


class FirestoreRepository:
    def __init__(
        self,
        collection: str = "personal_assistant",
        document_id: str = "default",
        project_id: str | None = None,
    ) -> None:
        self.collection = collection
        self.document_id = document_id
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
        self._client = self._build_client()

    def _build_client(self) -> Any:
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail="Firestore dependencies are not installed. Add google-cloud-firestore to requirements.",
            ) from exc
        try:
            if self.project_id:
                return firestore.Client(project=self.project_id)
            return firestore.Client()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not initialize Firestore client: {type(exc).__name__}: {exc}",
            ) from exc

    def _doc_ref(self) -> Any:
        return self._client.collection(self.collection).document(self.document_id)

    def load(self) -> DatabaseModel:
        snapshot = self._doc_ref().get()
        if not snapshot.exists:
            return self.save(DatabaseModel())
        payload = snapshot.to_dict() or {}
        return DatabaseModel.model_validate(payload)

    def save(self, data: DatabaseModel) -> DatabaseModel:
        self._doc_ref().set(data.model_dump(mode="json"))
        return data


def _safe_user_key(user_id: str | None) -> str:
    raw = (user_id or "default").strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)
    return safe or "default"


def create_repository(user_id: str | None = None) -> AssistantRepository:
    backend = (os.getenv("APP_STORAGE_BACKEND") or "json").strip().lower()
    if backend == "firestore":
        collection = os.getenv("FIRESTORE_COLLECTION", "personal_assistant")
        document_id = _safe_user_key(user_id or os.getenv("FIRESTORE_DOCUMENT_ID", "default"))
        return FirestoreRepository(collection=collection, document_id=document_id)
    if user_id:
        return JsonRepository(db_path=f"data/users/{_safe_user_key(user_id)}/assistant_db.json")
    return JsonRepository()

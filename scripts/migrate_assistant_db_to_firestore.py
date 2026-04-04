from __future__ import annotations

import argparse
import os
from pathlib import Path

from src.repository import FirestoreRepository, JsonRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy the local assistant_db.json contents into Firestore."
    )
    parser.add_argument(
        "--source",
        default="data/assistant_db.json",
        help="Path to the local assistant database JSON file.",
    )
    parser.add_argument(
        "--collection",
        default=os.getenv("FIRESTORE_COLLECTION", "personal_assistant"),
        help="Firestore collection name for the assistant document.",
    )
    parser.add_argument(
        "--document-id",
        default=os.getenv("FIRESTORE_DOCUMENT_ID", "default"),
        help="Firestore document id for the assistant database payload.",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT"),
        help="Optional Google Cloud project id override.",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"Source database file was not found: {source_path}")

    source_repo = JsonRepository(db_path=str(source_path))
    firestore_repo = FirestoreRepository(
        collection=args.collection,
        document_id=args.document_id,
        project_id=args.project_id,
    )

    data = source_repo.load()
    firestore_repo.save(data)

    print("Migration complete.")
    print(f"Source: {source_path}")
    print(f"Target collection: {args.collection}")
    print(f"Target document: {args.document_id}")
    print(
        "Counts: "
        f"{len(data.notes)} notes, "
        f"{len(data.tasks)} tasks, "
        f"{len(data.shopping_items)} shopping items, "
        f"{len(data.bills)} bills, "
        f"{len(data.events)} events, "
        f"{len(data.reminders)} reminders."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

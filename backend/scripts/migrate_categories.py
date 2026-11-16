"""Backfill legacy category names onto stable codes. Safe to run repeatedly."""
import json
from src.config.database import Base, engine, SessionLocal
from src.services.migration import migrate_all, verify
from src.services.schema import ensure_category_columns


def run():
    Base.metadata.create_all(engine)
    ensure_category_columns(engine)
    with SessionLocal() as db:
        summary = migrate_all(db)
        summary['verification'] = verify(db)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == '__main__':
    run()

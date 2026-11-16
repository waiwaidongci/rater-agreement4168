"""Additive schema upgrades for databases created before category versioning.

The project creates tables from ORM metadata, which cannot add columns to an
existing database. This helper only appends the nullable/defaulted columns the
category registry needs; it never drops or rewrites existing data, and running
it again is a no-op.
"""
from sqlalchemy import inspect, text

ADDITIONS = {
    'agreement_studies': [
        ('category_scheme', 'JSON NULL'),
    ],
    'rater_votes': [
        ('category_code', 'VARCHAR(40) NULL'),
    ],
    'agreement_runs': [
        ('category_locale', "VARCHAR(16) NOT NULL DEFAULT 'zh-CN'"),
        ('category_snapshot', 'JSON NULL'),
    ],
}

INDEXES = [
    ('rater_votes', 'ix_rater_votes_category_code', 'category_code'),
]


def ensure_category_columns(engine) -> dict:
    added = []
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table, columns in ADDITIONS.items():
            if table not in existing_tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns:
                if name in present:
                    continue
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                added.append({'table': table, 'column': name})
        existing_tables = set(inspect(connection).get_table_names())
        for table, index, column in INDEXES:
            if table not in existing_tables:
                continue
            fresh = inspect(connection)
            if column not in {c['name'] for c in fresh.get_columns(table)}:
                continue
            if index in {item['name'] for item in fresh.get_indexes(table)}:
                continue
            try:
                connection.execute(text(f'CREATE INDEX {index} ON {table} ({column})'))
                added.append({'table': table, 'index': index})
            except Exception:
                pass
    return {'added': added}

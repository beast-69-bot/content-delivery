"""
Alembic is intentionally disabled.

This project now uses MongoDB through Motor instead of SQLAlchemy models and
relational migrations.
"""

raise RuntimeError(
    "Alembic migrations are disabled because this project now uses MongoDB."
)

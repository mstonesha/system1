from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


DATABASE_URL = get_settings().require_database_url()

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    """Yield one SQLAlchemy session per request.

    Callers commit their own writes. This generator only closes
    the session; it does not commit at request end.
    """
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

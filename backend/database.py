import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Find the .env file in the FOCUSFLOW root folder
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

# Read database settings
database_url = URL.create(
    drivername="postgresql+psycopg",
    username=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT", "5432")),
    database=os.getenv("DB_NAME"),
)

# Create the database engine
engine = create_engine(
    database_url,
    pool_pre_ping=True,
)

# Create a reusable database session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# Base class for database models
class Base(DeclarativeBase):
    pass


# Give each request its own database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# Find the .env file in the main FOCUSFLOW folder
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

# Read the database settings from .env
required_settings = [
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
]

missing_settings = [
    name for name in required_settings if not os.getenv(name)
]

if missing_settings:
    raise ValueError(
        f"Missing settings in .env: {', '.join(missing_settings)}"
    )

# Build the database connection safely
database_url = URL.create(
    drivername="postgresql+psycopg",
    username=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT")),
    database=os.getenv("DB_NAME"),
)

engine = create_engine(database_url)

try:
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT current_database()")
        )
        database_name = result.scalar_one()

        print("Connection successful!")
        print(f"Connected database: {database_name}")

except Exception as error:
    print("Database connection failed.")
    print(f"Error: {error}")

finally:
    engine.dispose()

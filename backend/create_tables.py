
from database import Base, engine
import models  # Registers the User model with Base

# Create database tables that do not already exist
Base.metadata.create_all(bind=engine)

print("Database tables created successfully!")

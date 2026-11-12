import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from models import Base

# load_dotenv will read a local .env file, but override=False ensures 
# it NEVER overwrites variables already set in the Render dashboard.
load_dotenv(override=False)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

# INTERCEPT & FIX: Render provides URLs starting with 'postgres://', 
# but SQLAlchemy strictly requires 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create engine (removed the old psycopg2 timeout kwargs as we are moving to psycopg v3)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Auto-migrate tables
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Migration check failed: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

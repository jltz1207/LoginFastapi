from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from app.core.config import settings

# Create SQLAlchemy engine
# engine = create_engine(settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL, 
    echo=True,                                 # Optional: Logs all SQL to your terminal
    future=True                                # Forces SQLAlchemy 2.0 behaviors
)

# Create SessionLocal class
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()

# Dependency to get DB session
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
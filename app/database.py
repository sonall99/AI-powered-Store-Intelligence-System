from sqlmodel import create_engine, Session, SQLModel
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/store_intelligence.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

def init_db():
    os.makedirs("data", exist_ok=True)
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
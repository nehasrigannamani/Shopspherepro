
from sqlalchemy import text

from app.db.session import engine


def main():
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("Database connection successful:", result.scalar())
    except Exception as exc:
        print("Database connection failed.")
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()

from database import Base, engine
import models  # noqa: F401 - регистрирует модели


def main():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Done.")


if __name__ == "__main__":
    main()
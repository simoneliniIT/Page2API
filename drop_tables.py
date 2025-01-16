from app import db
import os

def drop_all_tables():
    print("Dropping all tables...")
    db.drop_all()
    db.session.commit()
    print("All tables dropped successfully")

if __name__ == "__main__":
    drop_all_tables() 
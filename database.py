from flask_sqlalchemy import SQLAlchemy
from flask import Flask
import os
from dotenv import load_dotenv

from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)

MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_DB = os.getenv("MYSQL_DB", "finance")

# SQLAlchemy connection string
# SQLAlchemy connection string
if MYSQL_USER and MYSQL_PASSWORD:
    try:
        import mysql.connector
        app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+mysqlconnector://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
    except ImportError:
        print("⚠️ mysql-connector-python not found. Falling back to SQLite.")
        base_dir = os.path.abspath(os.path.dirname(__file__))
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(base_dir, 'app.db')
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(base_dir, 'app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

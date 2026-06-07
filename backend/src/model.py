from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(100))
    category = db.Column(db.String(50)) # 'governance' or 'important'
    content = db.Column(db.Text)
    uploaded_by = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
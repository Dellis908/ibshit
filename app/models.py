from app import db

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(120), index=True)
    question_text = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(20), default='short_answer')
    correct_answer = db.Column(db.Text)
    marks = db.Column(db.Integer, default=1)
    image_path = db.Column(db.String(255))  # ścieżka do obrazka, jeśli jest

class UserAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, default=1)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'))
    user_response = db.Column(db.Text)
    is_correct = db.Column(db.Boolean)

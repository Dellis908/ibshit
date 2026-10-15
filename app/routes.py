from flask import Blueprint, render_template, request, jsonify
from app import db
from app.models import Question, UserAnswer
from fuzzywuzzy import fuzz

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    topics = db.session.query(Question.topic).distinct().all()
    return render_template('base.html', topics=[t[0] for t in topics])

@bp.route('/quiz/<topic>')
def quiz(topic):
    questions = Question.query.filter_by(topic=topic).all()
    return render_template('quiz.html', questions=questions, topic=topic)

@bp.route('/submit_answer', methods=['POST'])
def submit_answer():
    data = request.get_json()
    question = Question.query.get(data['question_id'])
    user_answer = data['answer'].strip()

    if question.question_type == 'short_answer':
        ratio = fuzz.ratio(question.correct_answer.lower(), user_answer.lower())
        is_correct = ratio > 80
    else:
        is_correct = question.correct_answer.lower() == user_answer.lower()

    answer = UserAnswer(
        question_id=question.id,
        user_response=user_answer,
        is_correct=is_correct
    )
    db.session.add(answer)
    db.session.commit()

    return jsonify({
        'is_correct': is_correct,
        'correct_answer': question.correct_answer
    })

"""学情分析 API — 查询 MySQL（去掉 Doris 依赖）"""
from flask import Blueprint, request, jsonify
from routes.auth import token_required, teacher_required
from models import User, ClassStudent, Subtask, TaskProgress, db

analytics_bp = Blueprint('analytics', __name__)


def _get_class_student_progress(class_id, course_id):
    """从 MySQL 查班级各学生对某课程的完成情况"""
    students = (
        db.session.query(User.id, User.display_name, User.username)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .filter(ClassStudent.class_id == class_id)
        .all()
    )
    subtask_ids = [r[0] for r in db.session.query(Subtask.id)
                   .filter(Subtask.course_id == course_id).all()]
    total = len(subtask_ids)
    result = []
    for sid, dname, uname in students:
        name = dname or uname
        if subtask_ids:
            completed = TaskProgress.query.filter(
                TaskProgress.student_id == sid,
                TaskProgress.subtask_id.in_(subtask_ids),
                TaskProgress.status == 'completed'
            ).count()
        else:
            # 该课程尚未生成子任务，完成数必然为 0
            # （注意：不能把空列表传给 filter()，SQLAlchemy 2.x 会抛 ArgumentError）
            completed = 0
        result.append({'name': name, 'completed': completed, 'total': total})
    result.sort(key=lambda x: -x['completed'])
    return result, len(students)


@analytics_bp.route('/class/<int:class_id>', methods=['GET'])
@teacher_required
def class_analytics(current_user, class_id):
    """班级课程学情：学生完成进度 + 概览"""
    course_id = request.args.get('course_id', type=int)
    if not course_id:
        return jsonify({'total_students': 0, 'avg_tasks': 0, 'complete_rate': 0,
                        'avg_chats': 0, 'top_done': [], 'top_duration': []})

    progress, total_students = _get_class_student_progress(class_id, course_id)
    if total_students == 0:
        return jsonify({'total_students': 0, 'avg_tasks': 0, 'complete_rate': 0,
                        'avg_chats': 0, 'top_done': [], 'top_duration': []})

    avg_completed = round(sum(p['completed'] for p in progress) / total_students, 1)
    max_completed = max(p['completed'] for p in progress) if progress else 1
    complete_rate = round(
        sum(1 for p in progress if p['completed'] >= p['total']) / total_students * 100
    ) if progress else 0

    top_done = [{'name': p['name'], 'count': p['completed']} for p in progress[:5]]
    top_duration = [{'name': p['name'], 'duration': 0} for p in progress[:5]]

    return jsonify({
        'total_students': total_students,
        'avg_tasks': avg_completed,
        'complete_rate': complete_rate,
        'avg_chats': 0,
        'top_done': top_done,
        'top_duration': top_duration,
    })

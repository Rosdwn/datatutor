from flask import Blueprint, request, jsonify
from models import User
from database import db
import jwt
import os
import uuid
import redis_client
from datetime import datetime, timedelta
from functools import wraps
from limiter_config import limiter
from captcha import generate_captcha, verify_captcha
from validators import validate_register_input, validate_login_input

auth_bp = Blueprint('auth', __name__)


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            token = request.args.get('token', '')
        if not token:
            return jsonify({'error': '未提供 Token'}), 401
        try:
            data = jwt.decode(token, os.getenv('SECRET_KEY', 'dev-secret'), algorithms=['HS256'])
            if redis_client.is_token_blacklisted(data.get('jti')):
                return jsonify({'error': 'Token 已登出'}), 401
            current_user = User.query.get(data['user_id'])
            if not current_user:
                return jsonify({'error': '用户不存在'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token 已过期'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': '无效 Token'}), 401
        return f(current_user, *args, **kwargs)
    return decorated


def teacher_required(f):
    @wraps(f)
    @token_required
    def decorated(current_user, *args, **kwargs):
        if current_user.role not in ('teacher', 'admin'):
            return jsonify({'error': '仅教师可操作'}), 403
        return f(current_user, *args, **kwargs)
    return decorated


# ===== 验证码接口 =====
@auth_bp.route('/captcha', methods=['GET'])
def get_captcha():
    """获取验证码图片"""
    captcha_id, b64_image = generate_captcha()
    return jsonify({
        'captcha_id': captcha_id,
        'image': b64_image
    })


@auth_bp.route('/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    data = request.get_json() or {}

    # 输入校验
    try:
        validate_login_input(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    user = User.query.filter_by(username=data['username']).first()
    if not user or not user.check_password(data['password']):
        return jsonify({'error': '用户名或密码错误'}), 401
    if user.role != 'admin' and user.role != data.get('role', user.role):
        return jsonify({'error': '角色不匹配'}), 403

    token = jwt.encode({
        'user_id': user.id,
        'role': user.role,
        'jti': str(uuid.uuid4()),
        'exp': datetime.utcnow() + timedelta(days=7)
    }, os.getenv('SECRET_KEY', 'dev-secret'), algorithm='HS256')
    if isinstance(token, bytes):
        token = token.decode('utf-8')

    redis_client.set_user_online(user.id, user.display_name or user.username)

    return jsonify({
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role,
            'display_name': user.display_name,
            'avatar_url': user.avatar_url,
            'student_id': user.student_id or '',
            'teacher_id': user.teacher_id or ''
        },
        'redirect': 'admin.html' if user.role == 'admin' else ('hub.html' if user.role == 'student' else 'teacher.html')
    })


@auth_bp.route('/register', methods=['POST'])
@limiter.limit("3 per minute")
def register():
    data = request.get_json() or {}

    # 输入校验
    try:
        validate_register_input(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # 验证码校验
    captcha_id = data.get('captcha_id', '')
    captcha_code = data.get('captcha_code', '')
    if not verify_captcha(captcha_id, captcha_code):
        return jsonify({'error': '验证码错误或已过期'}), 400

    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': '用户名已存在'}), 400
    role = data.get('role', 'student')
    user = User(
        username=data['username'],
        role=role,
        display_name=data.get('display_name', data['username']),
        teacher_id=data.get('teacher_id') if role == 'teacher' else None,
        student_id=data.get('student_id') if role == 'student' else None,
    )
    user.set_password(data['password'])
    db.session.add(user)
    db.session.commit()
    return jsonify({'message': '注册成功'}), 201


@auth_bp.route('/stats', methods=['GET'])
def stats():
    from models import User
    students = User.query.filter_by(role='student').count()
    teachers = User.query.filter_by(role='teacher').count()
    return jsonify({'students': students, 'teachers': teachers, 'total': students + teachers})


@auth_bp.route('/profile', methods=['GET'])
@token_required
def get_profile(current_user):
    """获取个人信息及统计数据"""
    from models import TaskProgress, Subtask, Report, Course, ChatMessage
    # 统计完成课程数（优先用 TaskProgress，兜底用 Report）
    completed_ids = db.session.query(TaskProgress.subtask_id).filter(
        TaskProgress.student_id == current_user.id,
        TaskProgress.status == 'completed'
    ).all()
    if completed_ids:
        course_count = db.session.query(Subtask.course_id).filter(
            Subtask.id.in_([r[0] for r in completed_ids])
        ).distinct().count()
    else:
        # 无进度数据时，从报告推算
        course_count = Report.query.filter_by(student_id=current_user.id).count()
    # 总实训时长（分钟）：优先 task_progress 真实耗时，报告兜底（2026-08-28 修复未生成报告时显示0）
    from datetime import datetime as _dt
    _total_sec = 0
    _progs = TaskProgress.query.filter_by(student_id=current_user.id).all()
    for _p in _progs:
        if _p.started_at and _p.completed_at:
            _total_sec += max(0, int((_p.completed_at - _p.started_at).total_seconds()))
        elif _p.status == 'in_progress' and _p.started_at:
            _total_sec += max(0, int((_dt.utcnow() - _p.started_at).total_seconds()))
    total_minutes = _total_sec // 60
    if total_minutes == 0:
        reports = Report.query.filter_by(student_id=current_user.id).all()
        total_minutes = int(sum(r.total_time_hours for r in reports))
    # 对话轮次 — 以 chat_messages 中 role='user' 的条数为准
    total_rounds = ChatMessage.query.filter_by(
        student_id=current_user.id,
        role='user'
    ).count()
    # 报告数
    report_count = len(reports)
    return jsonify({
        'id': current_user.id,
        'username': current_user.username,
        'display_name': current_user.display_name or current_user.username,
        'role': current_user.role,
        'avatar_url': current_user.avatar_url or '',
        'gender': current_user.gender or '',
        'school': current_user.school or '',
        'email': current_user.email or '',
        'course_count': course_count,
        'total_hours': total_minutes,
        'total_rounds': total_rounds,
        'report_count': report_count,
        'student_id': current_user.student_id or '',
        'teacher_id': current_user.teacher_id or '',
    })


@auth_bp.route('/profile', methods=['PUT'])
@token_required
def update_profile(current_user):
    """修改个人信息"""
    data = request.get_json()
    if 'display_name' in data:
        current_user.display_name = data['display_name']
    if 'gender' in data:
        current_user.gender = data['gender']
    if 'school' in data:
        current_user.school = data['school']
    if 'email' in data:
        current_user.email = data['email']
    db.session.commit()
    return jsonify({'message': '更新成功'})


@auth_bp.route('/logout', methods=['POST'])
@token_required
def logout(current_user):
    """登出（成员B Redis）：将当前 Token 的 jti 加入黑名单并清除在线状态"""
    auth_header = request.headers.get('Authorization', '')
    token_str = auth_header.replace('Bearer ', '') or request.args.get('token', '')
    if token_str:
        try:
            data = jwt.decode(token_str, os.getenv('SECRET_KEY', 'dev-secret'), algorithms=['HS256'], options={'verify_exp': False})
            jti = data.get('jti')
            exp = data.get('exp', 0)
            if jti:
                redis_client.blacklist_token(jti, exp)
        except Exception:
            pass
    redis_client.remove_user_online(current_user.id)
    return jsonify({'message': '已登出'})

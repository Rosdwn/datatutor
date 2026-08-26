"""学习进度路由 — 完整实现"""
from flask import Blueprint, request, jsonify
from database import db
from models import TaskProgress
from datetime import datetime
from routes.auth import token_required

progress_bp = Blueprint('progress', __name__)


@progress_bp.route('/<int:course_id>', methods=['GET'])
@token_required
def get_progress(current_user, course_id):
    """获取当前学生在某课程的进度"""
    from models import Subtask
    subtasks = Subtask.query.filter_by(course_id=course_id).order_by(Subtask.order_index).all()
    total = len(subtasks)

    # 查询所有子任务的状态
    all_progress = TaskProgress.query.filter(
        TaskProgress.student_id == current_user.id,
        TaskProgress.subtask_id.in_([s.id for s in subtasks])
    ).all()
    progress_map = {p.subtask_id: p.status for p in all_progress}

    # 构建 subtask_statuses: {id: 'pending'|'in_progress'|'completed'}
    subtask_statuses = {}
    completed = 0
    current_idx = 0
    for i, st in enumerate(subtasks):
        status = progress_map.get(st.id, 'pending')
        subtask_statuses[str(st.id)] = status
        if status == 'completed':
            completed += 1
        elif status == 'in_progress' and current_idx == 0:
            current_idx = i

    # 如果没有进行中的，指向第一个未完成的
    if current_idx == 0:
        for i, st in enumerate(subtasks):
            if progress_map.get(st.id, 'pending') != 'completed':
                current_idx = i
                break

    return jsonify({
        'completed': completed,
        'total': total,
        'subtask_statuses': subtask_statuses,
        'current_subtask_index': current_idx
    })


@progress_bp.route('/start', methods=['POST'])
@token_required
def start_subtask(current_user):
    """开始一个子任务"""
    data = request.get_json()
    existing = TaskProgress.query.filter_by(
        student_id=current_user.id,
        subtask_id=data['subtask_id']
    ).first()
    if existing:
        if existing.started_at is None:
            existing.started_at = datetime.utcnow()  # 已有开始时间不重置（2026-08-26 修复丢时长）
        existing.status = 'in_progress'
    else:
        tp = TaskProgress(
            student_id=current_user.id,
            subtask_id=data['subtask_id'],
            status='in_progress',
            started_at=datetime.utcnow(),
        )
        db.session.add(tp)
    db.session.commit()
    return jsonify({'message': '已开始'})


@progress_bp.route('/complete', methods=['POST'])
@token_required
def complete_subtask(current_user):
    """完成一个子任务"""
    data = request.get_json()
    tp = TaskProgress.query.filter_by(
        student_id=current_user.id,
        subtask_id=data['subtask_id']
    ).first()
    if tp:
        if tp.started_at is None:
            # 兜底：同课程前序任务完成时间；无前序用当前时间（2026-08-26）
            from models import Subtask as _Subtask
            cur = _Subtask.query.get(tp.subtask_id)
            prev = None
            if cur:
                prev = (TaskProgress.query
                        .join(_Subtask, TaskProgress.subtask_id == _Subtask.id)
                        .filter(TaskProgress.student_id == current_user.id,
                                TaskProgress.completed_at.isnot(None),
                                _Subtask.course_id == cur.course_id,
                                _Subtask.order_index < cur.order_index)
                        .order_by(_Subtask.order_index.desc()).first())
            tp.started_at = (prev.completed_at if prev and prev.completed_at else datetime.utcnow())
        tp.status = 'completed'
        tp.completed_at = datetime.utcnow()
    else:
        tp = TaskProgress(
            student_id=current_user.id,
            subtask_id=data['subtask_id'],
            status='completed',
            completed_at=datetime.utcnow(),
        )
        db.session.add(tp)
    db.session.commit()
    return jsonify({'message': '已完成'})


@progress_bp.route('/training_time', methods=['GET'])
@token_required
def get_training_time(current_user):
    """获取学生的实训时长（秒）和当前会话起始时间，支持按课程过滤"""
    from models import Subtask
    course_id = request.args.get('course_id', type=int)
    query = TaskProgress.query.filter_by(student_id=current_user.id)
    if course_id:
        subtask_ids = [s.id for s in Subtask.query.filter_by(course_id=course_id).all()]
        query = query.filter(TaskProgress.subtask_id.in_(subtask_ids))
    records = query.all()
    total_seconds = 0
    current_started_at = None
    _session_reset = False
    from models import Subtask as _Subtask
    _sub_cache = {}
    for r in records:
        start = r.started_at
        if start is None and r.completed_at:
            # 统计兜底：NULL 开始时间用同课程前序任务完成时间（2026-08-26）
            cur = _sub_cache.get(r.subtask_id) or _Subtask.query.get(r.subtask_id)
            _sub_cache[r.subtask_id] = cur
            if cur:
                prev = (TaskProgress.query
                        .join(_Subtask, TaskProgress.subtask_id == _Subtask.id)
                        .filter(TaskProgress.student_id == current_user.id,
                                TaskProgress.completed_at.isnot(None),
                                _Subtask.course_id == cur.course_id,
                                _Subtask.order_index < cur.order_index)
                        .order_by(_Subtask.order_index.desc()).first())
                if prev and prev.completed_at:
                    start = prev.completed_at
        if start and r.completed_at:
            delta = (r.completed_at - start).total_seconds()
            total_seconds += max(0, int(delta))
        if r.status == 'in_progress' and start:
            now = datetime.utcnow()
            if (now - start).total_seconds() > 1800:  # 会话超时30分钟：退出前端后不计时（2026-08-26）
                r.started_at = now
                start = now
                _session_reset = True
            current_started_at = start.isoformat() + 'Z'  # UTC 标记，修前端时区解析（2026-08-26）
            elapsed = (now - start).total_seconds()
            total_seconds += max(0, int(elapsed))
    if _session_reset:
        db.session.commit()
    return jsonify({
        'total_seconds': total_seconds,
        'current_started_at': current_started_at
    })


@progress_bp.route('/terminal_context', methods=['GET'])
@token_required
def get_terminal_context_route(current_user):
    """获取学生终端上下文（供工作流代码节点调用）"""
    from terminal_ws.terminal import get_terminal_context
    ctx = get_terminal_context(current_user.id)
    return jsonify({'context': ctx})

"""异步报告生成任务（成员D）

通过 Celery Worker 后台执行，并经 Flask-SocketIO + Redis 消息队列
向学生端实时推送进度（排队中 → 收集数据 → 调用AI → 保存 → 完成）。
前端同时保留轮询兜底，WebSocket 未命中时也能拿到最终结果。
"""
import os
import logging

from celery_app import celery_app
from database import db
from models import Report, Subtask, TaskProgress, ChatMessage, KnowledgeChat, Course
from routes.ai import call_xunfei_workflow, XUNFEI_FLOW_REPORT, call_maas
from metrics import reports_generated, ai_calls

logger = logging.getLogger(__name__)

# 仅用于 Worker 端的 SocketIO 实例：通过 Redis 消息队列把事件
# 路由到 Flask-SocketIO 服务端，再由服务端推给浏览器。
# （Flask-SocketIO 官方推荐的跨进程 emit 写法）
_socketio = None


def _get_socketio():
    global _socketio
    if _socketio is None:
        from flask_socketio import SocketIO
        redis_url = os.getenv(
            'SOCKETIO_MESSAGE_QUEUE',
            f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/2"
        )
        _socketio = SocketIO(message_queue=redis_url, async_mode='gevent')
    return _socketio


def _emit_progress(student_id, task_id, stage, message, extra=None):
    """向指定学生房间推送进度事件。失败不影响任务执行。"""
    payload = {
        'task_id': task_id,
        'stage': stage,        # started|collecting|generating|saving|completed|failed
        'message': message,
    }
    if extra:
        payload.update(extra)
    try:
        _get_socketio().emit(
            'report_progress', payload,
            room=f'student_{student_id}', namespace='/'
        )
    except Exception as e:
        logger.warning('SocketIO emit 失败（不影响任务）: %s', e)


@celery_app.task(bind=True, name='tasks.generate_report')
def generate_report_task(self, student_id, course_id):
    """后台异步生成实训报告，实时推送进度。"""
    task_id = self.request.id

    def progress(stage, message, extra=None):
        _emit_progress(student_id, task_id, stage, message, extra)

    try:
        # 0. 已开始
        progress('started', 'AI 正在生成实训报告...')

        from app import app
        with app.app_context():
            # 1. 收集数据
            progress('collecting', '正在收集实训数据...')
            course = Course.query.get(course_id)
            if not course:
                raise ValueError('课程不存在')

            subtasks = Subtask.query.filter_by(course_id=course_id).order_by(Subtask.order_index).all()
            subtask_ids = [s.id for s in subtasks]
            progresses = {
                p.subtask_id: p
                for p in TaskProgress.query.filter(
                    TaskProgress.student_id == student_id,
                    TaskProgress.subtask_id.in_(subtask_ids)
                ).all()
            }

            # 2. 组装 Prompt
            lines = [f'课程：{course.name}', f'目标：{course.description}', '']
            total_seconds = 0
            for st in subtasks:
                prog = progresses.get(st.id)
                status = '完成' if prog and prog.status == 'completed' else '已跳过（未计时）'
                minutes = 0
                if prog and prog.started_at and prog.completed_at:
                    _secs = int((prog.completed_at - prog.started_at).total_seconds())
                    minutes = round(_secs / 60, 1)
                    total_seconds += _secs
                lines.append(f'- [{status}] {st.name}（{minutes}分钟）')
            total_minutes = round(total_seconds / 60, 1)  # 秒累加后统一转分钟（2026-08-28；保留1位小数）
            lines.append(f'\n总耗时：{total_minutes}分钟')

            # 聊天摘要
            chats = ChatMessage.query.filter(
                ChatMessage.student_id == student_id,
                ChatMessage.subtask_id.in_(subtask_ids)
            ).order_by(ChatMessage.created_at).limit(20).all()
            if chats:
                lines.append('\n师生对话摘要：')
                for c in chats:
                    lines.append(f'[{c.role}] {c.content[:100]}')

            user_input = '\n'.join(lines)

            # 3. 调用 Agent
            progress('generating', 'AI 正在撰写报告，约需 10-30 秒...')
            ai_calls.labels(agent='report').inc()
            if XUNFEI_FLOW_REPORT:
                result = call_xunfei_workflow(user_input, XUNFEI_FLOW_REPORT)
            else:
                result = call_maas(user_input, max_tokens=2000)

            if result.get('error'):
                raise RuntimeError(result['error'])

            # 4. 存报告
            progress('saving', '报告生成完成，正在保存...')
            report = Report(
                student_id=student_id,
                course_id=course_id,
                content=result['content'],
                total_time_hours=total_minutes,
            )
            db.session.add(report)
            db.session.commit()

            reports_generated.inc()

            # 5. 完成 → WebSocket 推送最终结果
            progress('completed', '报告已生成', {
                'report_id': report.id,
                'content': result['content'],
                'total_hours': total_minutes,
            })
            return {
                'report_id': report.id,
                'content': result['content'],
                'total_hours': total_minutes,
            }

    except Exception as e:
        logger.exception('报告生成失败 task_id=%s', task_id)
        progress('failed', f'生成失败: {e}')
        raise


def _generate_report_sync(student_id, course_id):
    """同步生成报告（无 Celery）返回 (content, grade, total_minutes)"""
    from app import app
    with app.app_context():
        course = Course.query.get(course_id)
        if not course:
            raise ValueError('课程不存在')
        subtasks = Subtask.query.filter_by(course_id=course_id).order_by(Subtask.order_index).all()
        subtask_ids = [s.id for s in subtasks]
        progresses = {p.subtask_id: p for p in TaskProgress.query.filter(
            TaskProgress.student_id == student_id,
            TaskProgress.subtask_id.in_(subtask_ids)).all()}
        lines = [f'课程：{course.name}', f'目标：{course.description}', '']
        total_seconds = 0; completed_count = 0
        for st in subtasks:
            prog = progresses.get(st.id)
            status = '完成' if prog and prog.status == 'completed' else '未完成'
            if prog and prog.status == 'completed': completed_count += 1
            minutes = 0
            if prog and prog.started_at and prog.completed_at:
                _secs = int((prog.completed_at - prog.started_at).total_seconds())
                minutes = round(_secs / 60, 1)
                total_seconds += _secs
            lines.append(f'- [{status}] {st.name}（{minutes}分钟）')
        total_minutes = round(total_seconds / 60, 1)  # 秒累加后统一转分钟（2026-08-28；保留1位小数）
        lines.append(f'\n总耗时：{total_minutes}分钟 | 完成率：{completed_count}/{len(subtasks)}')
        chats = ChatMessage.query.filter(ChatMessage.student_id == student_id,
            ChatMessage.subtask_id.in_(subtask_ids)).order_by(ChatMessage.created_at).limit(20).all()
        if chats:
            lines.append('\n师生对话摘要：')
            for c in chats: lines.append(f'[{c.role}] {c.content[:100]}')
        user_input = '\n'.join(lines)
        if XUNFEI_FLOW_REPORT:
            result = call_xunfei_workflow(user_input, XUNFEI_FLOW_REPORT)
        else:
            result = call_maas(user_input, max_tokens=2000)
        if result.get('error'): raise RuntimeError(result['error'])
        # 从 AI 输出中提取等级（如 "**等级**：D"）
        import re
        m = re.search(r'等级\**\s*[：:]\s*([A-F])', result['content'])
        if m: grade = m.group(1)
        else:
            rate = completed_count / len(subtasks) if subtasks else 0
            if rate >= 0.9: grade = 'A'
            elif rate >= 0.8: grade = 'B'
            elif rate >= 0.6: grade = 'C'
            elif rate >= 0.4: grade = 'D'
            else: grade = 'F'
        report = Report(student_id=student_id, course_id=course_id,
            content=result['content'], total_time_hours=total_minutes, grade=grade)
        db.session.add(report); db.session.commit()
        return result['content'], grade, total_minutes

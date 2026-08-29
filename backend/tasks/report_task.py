"""同步报告生成（成员D）

报告生成由后端同步执行：基于学生真实实训过程数据（子任务状态、耗时、对话摘要）
调用 Report Agent 生成实训报告，并按完成率评定等级。
"""
from database import db
from models import Report, Subtask, TaskProgress, ChatMessage, Course
from routes.ai import call_xunfei_workflow, XUNFEI_FLOW_REPORT, call_maas


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
        # 从 AI 输出中提取等级（兼容 "等级：D" / "综合评价：D级" / "评级：D" 等写法，2026-08-29 修解析失败回退完成率致评级虚高）
        import re
        grade = None
        for _pat in (r'(?:等级|评级|综合评价|综合评级|总评)\s*\*{0,2}\s*[：:]\s*([A-D])',
                     r'(?:等级|评级|综合评价|综合评级|总评)[^\n]{0,6}?([A-D])\s*级',
                     r'([A-D])\s*级'):
            _m = re.search(_pat, result['content'])
            if _m:
                grade = _m.group(1)
                break
        if not grade:
            rate = completed_count / len(subtasks) if subtasks else 0
            if rate >= 0.9: grade = 'A'
            elif rate >= 0.8: grade = 'B'
            elif rate >= 0.6: grade = 'C'
            else: grade = 'D'
        report = Report(student_id=student_id, course_id=course_id,
            content=result['content'], total_time_hours=total_minutes, grade=grade)
        db.session.add(report); db.session.commit()
        return result['content'], grade, total_minutes

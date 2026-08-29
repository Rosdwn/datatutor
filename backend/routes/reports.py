"""报告路由（成员D）"""
from flask import Blueprint, request, jsonify
from routes.auth import token_required, teacher_required
from models import db, Report, Course
import io

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/generate', methods=['POST'])
@token_required
def generate_report(current_user):
    """同步生成课程实训报告（无 Celery）"""
    data = request.get_json()
    course_id = data.get('course_id')
    if not course_id:
        return jsonify({'error': '缺少课程ID'}), 400

    try:
        from tasks.report_task import _generate_report_sync
        content, grade, total_hours = _generate_report_sync(current_user.id, course_id)
        return jsonify({'message': '报告已生成', 'content': content, 'grade': grade})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_bp.route('', methods=['GET'])
@token_required
def list_reports(current_user):
    """获取报告列表，支持 ?course_id= 过滤"""
    course_id = request.args.get('course_id', type=int)
    if current_user.role == 'teacher':
        q = Report.query.order_by(Report.created_at.desc())
        if course_id: q = q.filter_by(course_id=course_id)
        reports = q.all()
    else:
        q = Report.query.filter_by(student_id=current_user.id)
        if course_id: q = q.filter_by(course_id=course_id)
        reports = q.order_by(Report.created_at.desc()).all()
    return jsonify({'reports': [{
        'id': r.id,
        'course_id': r.course_id,
        'course_name': r.course.name if r.course else '',
        'student_id': r.student_id,
        'grade': r.grade,
        'student_id': r.student_id,
        'total_time_hours': r.total_time_hours,
        'content': r.content,
        'preview': (r.content or '')[:100],
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')
    } for r in reports]})


@reports_bp.route('/<int:report_id>/download', methods=['GET'])
@token_required
def download_report(current_user, report_id):
    """下载报告为 txt 文件"""
    report = Report.query.get_or_404(report_id)
    if current_user.role != 'teacher' and report.student_id != current_user.id:
        return jsonify({'error': '无权访问'}), 403
    bio = io.BytesIO()
    bio.write(report.content.encode('utf-8'))
    bio.seek(0)
    from flask import send_file
    return send_file(bio, mimetype='text/markdown', as_attachment=True, download_name=f'report_{report_id}.md')


@reports_bp.route('/student/<int:student_id>', methods=['GET'])
@teacher_required
def student_reports(current_user, student_id):
    """教师查看某学生报告"""
    reports = Report.query.filter_by(student_id=student_id).order_by(Report.created_at.desc()).all()
    return jsonify({'reports': [{
        'id': r.id,
        'course_id': r.course_id,
        'course_name': r.course.name if r.course else '',
        'content': r.content,
        'total_time_hours': r.total_time_hours,
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')
    } for r in reports]})

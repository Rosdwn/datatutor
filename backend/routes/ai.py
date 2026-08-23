"""讯飞星火工作流 Agent API — REST HTTPS"""
from flask import Blueprint, request, jsonify
from routes.auth import token_required
import json
import os
import requests
import uuid
import redis_client

ai_bp = Blueprint('ai', __name__)

XUNFEI_API_KEY = os.getenv('XUNFEI_API_KEY', '')
XUNFEI_API_SECRET = os.getenv('XUNFEI_API_SECRET', '')
# 四个 Agent 的 Flow ID（从 .env 读取）
XUNFEI_FLOW_TRAINING = os.getenv('XUNFEI_FLOW_TRAINING', '')     # 实训副驾
XUNFEI_FLOW_KNOWLEDGE = os.getenv('XUNFEI_FLOW_KNOWLEDGE', '')   # 知识问答
XUNFEI_FLOW_TEACHER = os.getenv('XUNFEI_FLOW_TEACHER', '')       # 课程生成
XUNFEI_FLOW_REPORT   = os.getenv('XUNFEI_FLOW_REPORT', '')       # 报告生成
XUNFEI_WORKFLOW_URL = 'https://xingchen-api.xf-yun.com/workflow/v1/chat/completions'

# 千问兜底（已弃用，改用讯飞星辰MaaS）
# SILICONFLOW_API_KEY = os.getenv('SILICONFLOW_API_KEY', '')
# SILICONFLOW_URL = 'https://api.siliconflow.cn/v1/chat/completions'
# SILICONFLOW_MODEL = os.getenv('SILICONFLOW_MODEL', 'Qwen/Qwen3-8B')

# 讯飞星辰 MaaS（OpenAI 兼容接口，替代千问兜底）
XUNFEI_MAAS_URL = 'https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions'
XUNFEI_MAAS_AUTH = XUNFEI_API_KEY + ':' + XUNFEI_API_SECRET
XUNFEI_MAAS_MODEL = 'xop35qwen2b'  # Qwen3.5-2B（免费）


def call_xunfei_workflow(user_input, flow_id, history=None):
    """调用讯飞工作流 Agent API（非流式）"""
    if history is None:
        history = []

    headers = {
        'Authorization': f'Bearer {XUNFEI_API_KEY}:{XUNFEI_API_SECRET}',
        'Content-Type': 'application/json',
    }

    body = {
        'flow_id': flow_id,
        'uid': str(uuid.uuid4())[:8],
        'parameters': {
            'AGENT_USER_INPUT': user_input,
        },
        'stream': True,
        'history': history,
    }

    try:
        resp = requests.post(XUNFEI_WORKFLOW_URL, json=body, headers=headers, timeout=120, stream=True)
        resp.raise_for_status()

        content = ''
        usage = None
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8').strip()
            if not line.startswith('data:'):
                continue
            data_str = line[5:].strip()
            if data_str == '[DONE]':
                break
            try:
                chunk = json.loads(data_str)
                if chunk.get('code') != 0:
                    return {'content': '', 'error': chunk.get('message', 'xfyun error'), 'usage': None}
                choices = chunk.get('choices', [])
                for c in choices:
                    content += c.get('delta', {}).get('content', '')
                u = chunk.get('usage')
                if u:
                    usage = u
            except json.JSONDecodeError:
                continue

        # 工作流结束节点可能返回 JSON 包裹的输出，提取实际内容
        if content.strip().startswith('{') and '"output"' in content:
            try:
                parsed = json.loads(content)
                content = parsed.get('output', content)
            except json.JSONDecodeError:
                pass

        return {'content': content, 'error': None, 'usage': usage}

    except Exception as e:
        return {'content': '', 'error': str(e), 'usage': None}


def call_maas(user_input, max_tokens=800):
    """讯飞星辰 MaaS 兜底（OpenAI 兼容接口）"""
    try:
        resp = requests.post(
            XUNFEI_MAAS_URL,
            headers={
                'Authorization': f'Bearer {XUNFEI_MAAS_AUTH}',
                'Content-Type': 'application/json'
            },
            json={
                'model': XUNFEI_MAAS_MODEL,
                'messages': [{'role': 'user', 'content': user_input}],
                'max_tokens': max_tokens,
                'temperature': 0.7,
                'stream': False
            },
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        if 'choices' in data and len(data['choices']) > 0:
            return {'content': data['choices'][0]['message']['content'], 'error': None, 'usage': None}
        return {'content': '', 'error': data.get('error', {}).get('message', 'maas error'), 'usage': None}
    except Exception as e:
        return {'content': '', 'error': str(e), 'usage': None}


def format_history(messages):
    """将 [{role, content}] 转为讯飞 history 格式 [{role, content_type: 'text', content}]"""
    result = []
    for m in messages:
        result.append({
            'role': m['role'],
            'content_type': 'text',
            'content': m['content'],
        })
    return result


# ===== 路由 =====

@ai_bp.route('/chat', methods=['POST'])
@token_required
def ai_chat(current_user):
    import traceback
    try:
        from terminal_ws.terminal import get_terminal_context
        data = request.get_json()
        messages = data.get('messages', [])

        # 注入终端上下文 + 课程文件信息
        term_ctx = get_terminal_context(current_user.id)
        file_ctx = ''
        try:
            course_id = data.get('course_id') or (messages[0].get('course_id') if messages else None)
            if course_id:
                from routes.courses import get_course_files_for_ai
                file_ctx = get_course_files_for_ai(course_id)
        except Exception:
            pass

        if (term_ctx or file_ctx) and messages:
            ctx = f'[终端环境]\n{term_ctx}\n{file_ctx}\n[终端环境结束]\n\n学生说：'
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]['role'] == 'user':
                    messages[i]['content'] = ctx + messages[i]['content']
                    break

        # 提取系统提示词（讯飞 API 不支持 system role，拼入用户消息）
        system_prompt = ''
        for m in messages:
            if m['role'] == 'system':
                system_prompt = m['content']
                break

        # 最后一条用户消息作为当前输入
        user_input = ''
        history = []
        for i in range(len(messages)):
            m = messages[i]
            if m['role'] == 'user':
                if i == len(messages) - 1:
                    user_input = m['content']
                else:
                    history.append({'role': 'user', 'content_type': 'text', 'content': m['content']})
            elif m['role'] == 'assistant':
                history.append({'role': 'assistant', 'content_type': 'text', 'content': m['content']})

        # 将系统提示词前置到用户输入（让工作流 Agent 看到任务上下文）
        if system_prompt:
            user_input = system_prompt + '\n\n' + user_input

        # Redis 缓存已关闭（教学场景需实时，2026-08-23 大王拍板）
        if XUNFEI_FLOW_TRAINING:
            result = call_xunfei_workflow(user_input, XUNFEI_FLOW_TRAINING, history)
        else:
            result = call_maas(user_input, max_tokens=2000)
        if result['error']:
            return jsonify({'error': result['error']}), 500

        
        # 自动保存对话到数据库（供报告生成使用）
        subtask_id = data.get('subtask_id')
        if subtask_id and current_user.role == 'student':
            try:
                from models import ChatMessage, db as chat_db
                chat_db.session.add(ChatMessage(student_id=current_user.id, subtask_id=subtask_id, role='user', content=user_input[:2000]))
                chat_db.session.add(ChatMessage(student_id=current_user.id, subtask_id=subtask_id, role='assistant', content=result['content'][:2000]))
                chat_db.session.commit()
            except Exception:
                pass
        
        return jsonify({'reply': result['content'], 'usage': result['usage']})
    except Exception as e:
        print(f'[ai_chat ERROR] {e}')
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@ai_bp.route('/knowledge', methods=['POST'])
@token_required
def ai_knowledge(current_user):
    try:
        from terminal_ws.terminal import get_terminal_context
        data = request.get_json()
        messages = data.get('messages', [])
        # 提取 system prompt
        sys_p = ''
        last_user = ''
        for m in messages:
            if m['role'] == 'system':
                sys_p = m['content']
            elif m['role'] == 'user':
                last_user = m['content']
        if sys_p:
            last_user = sys_p + '\n\n' + last_user
        # 注入终端上下文
        term_ctx = get_terminal_context(current_user.id)
        if term_ctx:
            last_user = f'[终端环境]\n{term_ctx}\n[终端环境结束]\n\n学生问：{last_user}'

        # Redis 缓存已关闭（教学场景需实时，2026-08-23 大王拍板）
        if XUNFEI_FLOW_KNOWLEDGE:
            result = call_xunfei_workflow(last_user, XUNFEI_FLOW_KNOWLEDGE)
        else:
            result = call_maas(last_user)
        if result['error']:
            return jsonify({'error': result['error']}), 500

        return jsonify({'reply': result['content']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@ai_bp.route('/generate-course', methods=['POST'])
@token_required
def ai_generate_course(current_user):
    """AI 生成课程 — 返回结构化课程数据 (name, description, subtasks)"""
    import json as _json, re
    prompt = ''
    file_preview = ''
    if request.content_type and 'multipart' in request.content_type:
        prompt = request.form.get('prompt', '')
        # 读取上传文件的前几行内容，嵌入 prompt 发给智能体
        uploaded_file = request.files.get('file')
        if uploaded_file and uploaded_file.filename:
            try:
                raw = uploaded_file.read(5000)  # 最多读 5KB
                # 尝试 UTF-8 解码
                text = raw.decode('utf-8', errors='replace')
                lines = text.strip().split('\n')
                # 取表头 + 前9行数据
                preview_lines = lines[:10]
                file_preview = '\n'.join(preview_lines)
                if len(preview_lines) < len(lines):
                    file_preview += f'\n... (共 {len(lines)} 行)'
                prompt = f'数据文件 {uploaded_file.filename} 预览（前10行）：\n{file_preview}\n\n课程主题：{prompt}'
            except Exception:
                pass
    else:
        data = request.get_json(silent=True) or {}
        prompt = data.get('prompt', data.get('topic', ''))
        # 支持 JSON 传文件内容（前端用 FileReader 读取后通过 JSON body 发送）
        file_content = data.get('file_content', '')
        file_name = data.get('file_name', '')
        if file_content and file_name:
            lines = file_content.strip().split('\n')
            preview_lines = lines[:10]
            file_preview = '\n'.join(preview_lines)
            if len(preview_lines) < len(lines):
                file_preview += f'\n... (共 {len(lines)} 行)'
            prompt = f'数据文件 {file_name} 预览（前10行）：\n{file_preview}\n\n课程主题：{prompt}'

    if not prompt:
        return jsonify({'error': '请输入课程主题'}), 400

    if XUNFEI_FLOW_TEACHER:
        result = call_xunfei_workflow(prompt, XUNFEI_FLOW_TEACHER)
    else:
        result = call_maas(prompt, max_tokens=2000)

    if result['error']:
        return jsonify({'error': result['error']}), 500

    content = result['content']
    import logging
    logging.warning(f"[AI-GEN-COURSE] RAW content ({len(content)} chars): {content[:500]}")

    # Unwrap workflow output wrapper if present
    if content.strip().startswith('{') and '"output"' in content:
        try:
            wrapped = json.loads(content)
            content = wrapped.get('output', content)
        except:
            pass

    # Try JSON first
    try:
        parsed = json.loads(content)
        return jsonify({
            'name': parsed.get('name', ''),
            'description': parsed.get('description', ''),
            'subtasks': parsed.get('subtasks', []),
        })
    except:
        pass

    # Markdown parser: extract structured data from FLOW_TEACHER output
    # Format: ## 实训主题 / ## 实训目标 / ### N. 任务名 **command** **expected_output** **knowledge_text**
    import re
    name = prompt
    desc = ''
    subtasks = []

    # Extract course name
    m = re.search(r'##\s*实训主题\s*\n\s*(.+?)\n', content)
    if m:
        name = m.group(1).strip()
    # Extract description
    m = re.search(r'##\s*实训目标\s*\n(.*?)(?=\n###\s|\Z)', content, re.DOTALL)
    if m:
        desc = m.group(1).strip()

    # Extract subtasks with full fields
    task_blocks = re.split(r'\n(?=###\s*\d+\.)', content)
    for block in task_blocks:
        task_name = ''
        m = re.search(r'###\s*\d+\.\s*(.+)', block)
        if m:
            task_name = m.group(1).strip()
        if not task_name:
            continue

        # Extract command (```bash or ``` code blocks after **command**)
        cmd = ''
        m = re.search(r'\*\*command\*\*[：:]\s*\n```(?:bash)?\s*\n(.*?)```', block, re.DOTALL)
        if m:
            cmd = m.group(1).strip()

        # Extract expected_output
        expected = ''
        m = re.search(r'\*\*expected_output\*\*[：:]\s*\n```\s*\n(.*?)```', block, re.DOTALL)
        if m:
            expected = m.group(1).strip()

        # Extract knowledge_text
        knowledge = ''
        m = re.search(r'\*\*knowledge_text\*\*[：:]\s*\n(.*?)(?=\n###|\Z)', block, re.DOTALL)
        if m:
            knowledge = m.group(1).strip()

        subtasks.append({
            'name': task_name,
            'command': cmd,
            'expected_output': expected,
            'knowledge_text': knowledge,
        })

    # If no subtasks found with markdown format, fall back to simple line parsing
    if not subtasks:
        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        for line in lines:
            m = re.match(r'^(\d+)[\.\)、]\s*(.+)', line)
            if m and len(m.group(2)) > 3:
                subtasks.append({'name': m.group(2).strip(), 'command': '', 'expected_output': '', 'knowledge_text': ''})
        if lines and not subtasks:
            name = lines[0].strip().lstrip('#- *').strip()
            desc = '\n'.join(lines[1:5]) if len(lines) > 1 else ''

    return jsonify({
        'name': name.strip() or prompt,
        'description': desc.strip(),
        'subtasks': subtasks,
    })

@ai_bp.route('/generate', methods=['POST'])
@token_required
def ai_generate(current_user):
    """教师端 AI 生成实训任务（Teacher Agent）"""
    try:
        data = request.get_json()
        topic = data.get('topic', '')
        course_id = data.get('course_id')
        if not topic:
            return jsonify({'error': '请输入课程主题'}), 400

        # 用户提示词（系统提示词已由工作流内置）
        user_prompt = topic

        # 注入课程文件信息
        if course_id:
            from routes.courses import get_course_files_for_ai
            file_info = get_course_files_for_ai(course_id)
            if file_info:
                user_prompt += f'\n\n作为参考，该课程已上传以下数据文件（前10行预览）：\n{file_info}'

        if XUNFEI_FLOW_TEACHER:
            result = call_xunfei_workflow(user_prompt, XUNFEI_FLOW_TEACHER)
        else:
            result = call_maas(user_prompt, max_tokens=2000)
        if result['error']:
            return jsonify({'error': result['error']}), 500
        return jsonify({'reply': result['content']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

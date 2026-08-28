/**
 * DataTutor 前端 API 封装层 v3.0
 * 4人协作合并版 — AI对话/知识/报告/个人中心/Toast通知
 */

const API_BASE = '/api';

// ========== Toast 通知系统（成员D贡献） ==========
function ensureToastContainer() {
    let c = document.getElementById('datatutor-toast-container');
    if (!c) {
        c = document.createElement('div');
        c.id = 'datatutor-toast-container';
        c.style.cssText = 'position:fixed;top:24px;right:24px;z-index:99999;display:flex;flex-direction:column;gap:10px;pointer-events:none;max-width:380px;';
        document.body.appendChild(c);
    }
    return c;
}
function toast(message, type) {
    type = type || 'info';
    var c = ensureToastContainer();
    var el = document.createElement('div');
    var colors = {
        error: { bg: '#FEF2F2', border: '#FECACA', fg: '#B91C1C', icon: '⚠' },
        success: { bg: '#F0FDF4', border: '#BBF7D0', fg: '#15803D', icon: '✓' },
        info: { bg: '#EFF6FF', border: '#BFDBFE', fg: '#1D4ED8', icon: 'ℹ' },
        warn: { bg: '#FFFBEB', border: '#FDE68A', fg: '#92400E', icon: '⚡' },
    };
    var col = colors[type] || colors.info;
    el.style.cssText = 'display:flex;align-items:center;gap:8px;padding:12px 16px;border-radius:10px;font-size:13px;font-family:Inter,sans-serif;box-shadow:0 4px 24px rgba(0,0,0,.08);pointer-events:auto;animation:slideInRight .3s ease;background:' + col.bg + ';border:1px solid ' + col.border + ';color:' + col.fg;
    el.innerHTML = '<span style="font-size:16px">' + col.icon + '</span><span style="flex:1">' + message + '</span>';
    c.appendChild(el);
    setTimeout(function() {
        el.style.opacity = '0'; el.style.transition = 'opacity 0.3s';
        setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
    }, 3500);
}

// ============================================================
// Token 管理
// ============================================================
function getToken() {
  return localStorage.getItem('datatutor_token');
}

// ============================================================
// 通用请求封装 —— 401 自动跳转登录页
// ============================================================
async function apiFetch(url, options) {
  options = options || {};
  var token = getToken();
  var headers = { 'Content-Type': 'application/json' };
  if (options.headers) Object.assign(headers, options.headers);
  if (token) headers['Authorization'] = 'Bearer ' + token;
  var res = await fetch(API_BASE + url, Object.assign({}, options, { headers: headers, cache: 'no-store' }));
  if (res.status === 401) {
    localStorage.removeItem('datatutor_token');
    localStorage.removeItem('datatutor_user');
    window.location.href = 'login.html';
    throw new Error('未授权');
  }
  // 处理 204 No Content 等空响应体
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    if (!res.ok) throw new Error('请求失败');
    return {};
  }
  var text = await res.text();
  var data;
  try { data = JSON.parse(text); } catch(e) { data = { _raw: text }; }
  if (!res.ok) throw new Error(data.error || data.message || '请求失败 (' + res.status + ')');
  return data;
}

// ============================================================
// API 命名空间 —— 严格按契约方法签名
// ============================================================
var API = {
  // ---- 登录 ----
  login: function(username, password, role) {
    return apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username, password: password, role: role })
    });
  },

  // ---- 注册 ----
  register: function(username, password, displayName, role, extra) {
    var body = { username: username, password: password, display_name: displayName, role: role };
    if (extra) body[role === 'teacher' ? 'teacher_id' : 'student_id'] = extra;
    return apiFetch('/auth/register', {
      method: 'POST',
      body: JSON.stringify(body)
    });
  },

  // ---- 登出 ----
  logout: function() {
    localStorage.removeItem('datatutor_token');
    localStorage.removeItem('datatutor_user');
    window.location.href = 'login.html';
  },

  // ---- 当前用户 ----
  getCurrentUser: function() {
    var raw = localStorage.getItem('datatutor_user');
    if (!raw) return null;
    try { return JSON.parse(raw); } catch(e) { return null; }
  },

  // ---- 课程 ----
  getCourses: function(role) {
    return apiFetch('/courses?role=' + (role || 'student'));
  },

  getCourseDetail: function(id) {
    return apiFetch('/courses/' + id);
  },

  createCourse: function(name, desc, isPublic) {
    return apiFetch('/courses', {
      method: 'POST',
      body: JSON.stringify({ name: name, description: desc, is_public: isPublic || false })
    });
  },

  updateCourse: function(id, name, desc, isPublic) {
    return apiFetch('/courses/' + id, {
      method: 'PUT',
      body: JSON.stringify({ name: name, description: desc, is_public: isPublic })
    });
  },

  deleteCourse: function(id) {
    return apiFetch('/courses/' + id, { method: 'DELETE' });
  },

  // ---- 子任务 ----
  getSubtasks: function(courseId) {
    return apiFetch('/courses/' + courseId + '/subtasks');
  },

  // ---- 课程文件 ----
  uploadCourseFile: function(courseId, file) {
    var formData = new FormData();
    formData.append('file', file);
    var token = getToken();
    var headers = {};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return fetch(API_BASE + '/courses/' + courseId + '/files', {
      method: 'POST',
      headers: headers,
      body: formData
    }).then(function(res) {
      if (res.status === 401) { localStorage.removeItem('datatutor_token'); window.location.href = 'login.html'; throw new Error('未授权'); }
      return res.json();
    });
  },
  listCourseFiles: function(courseId) {
    return apiFetch('/courses/' + courseId + '/files');
  },
  downloadCourseFile: function(courseId, filename) {
    var token = getToken();
    var url = API_BASE + '/courses/' + courseId + '/files/' + encodeURIComponent(filename) + '/download';
    // 用隐藏a标签触发下载，带上token
    var a = document.createElement('a');
    a.href = url + '?token=' + token;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  saveSubtasks: function(courseId, subtasks) {
    return apiFetch('/courses/' + courseId + '/subtasks', {
      method: 'POST',
      body: JSON.stringify({ subtasks: subtasks })
    });
  },

  // ---- 班级 ----
  getClassList: function() {
    return apiFetch('/classes/list');
  },

  // 兼容旧版——按课程 ID 查班级（新版无此语义，返回空）
  getClassesByCourse: function(courseId) {
    return Promise.resolve([]);
  },

  deleteClass: function(classId) {
    return apiFetch('/classes/' + classId, { method: 'DELETE' });
  },

  createClass: function(name, classCode) {
    return apiFetch('/classes/create', {
      method: 'POST',
      body: JSON.stringify({ name: name, class_code: classCode })
    });
  },

  getClassCourses: function(classId) {
    return apiFetch('/courses/class/' + classId);
  },

  assignCourseToClass: function(classId, courseId) {
    return apiFetch('/classes/assign_course', {
      method: 'POST',
      body: JSON.stringify({ class_id: classId, course_id: courseId })
    });
  },

  removeCourseFromClass: function(classId, courseId) {
    return apiFetch('/classes/remove_course', {
      method: 'POST',
      body: JSON.stringify({ class_id: classId, course_id: courseId })
    });
  },

  joinClass: function(classCode, teacherName) {
    return apiFetch('/classes/join', {
      method: 'POST',
      body: JSON.stringify({ class_code: classCode, teacher_name: teacherName || '' })
    });
  },

  getClassProgress: function(classId, courseId) {
    var url = '/classes/' + classId + '/progress';
    if (courseId) url += '?course_id=' + courseId;
    return apiFetch(url);
  },

  // ---- 进度 ----
  getProgress: function(courseId) {
    return apiFetch('/progress/' + courseId);
  },

  startSubtask: function(subtaskId) {
    return apiFetch('/progress/start', {
      method: 'POST',
      body: JSON.stringify({ subtask_id: subtaskId })
    });
  },

  completeSubtask: function(subtaskId) {
    return apiFetch('/progress/complete', {
      method: 'POST',
      body: JSON.stringify({ subtask_id: subtaskId })
    });
  },

  getTrainingTime: function(courseId) {
    var url = '/progress/training_time';
    if (courseId) url += '?course_id=' + courseId;
    return apiFetch(url);
  },

  // ---- 对话历史 ----
  getChatHistory: function(subtaskId) {
    return apiFetch('/chat/history/' + subtaskId);
  },

  saveChatMessage: function(subtaskId, role, content) {
    return apiFetch('/chat/save', {
      method: 'POST',
      body: JSON.stringify({ subtask_id: subtaskId, role: role, content: content })
    });
  },

  // ---- 知识面板对话 ----
  getKnowledgeChats: function(subtaskId) {
    return apiFetch('/chat/knowledge/' + subtaskId);
  },

  saveKnowledgeChat: function(subtaskId, role, content) {
    return apiFetch('/chat/knowledge/save', {
      method: 'POST',
      body: JSON.stringify({ subtask_id: subtaskId, role: role, content: content })
    });
  },

  // ---- AI 代理 ----
  aiChat: function(messages, courseId) {
    return apiFetch('/ai/chat', {
      method: 'POST',
      body: JSON.stringify({ messages: messages, course_id: courseId })
    });
  },

  aiGenerate: function(topic, courseId) {
    return apiFetch('/ai/generate', {
      method: 'POST',
      body: JSON.stringify({ topic: topic, course_id: courseId })
    });
  },

  aiKnowledge: function(messages) {
    return apiFetch('/ai/knowledge', {
      method: 'POST',
      body: JSON.stringify({ messages: messages })
    });
  },

  // ---- 实训报告 ----
  generateReport: function(courseId) {
    return apiFetch('/reports/generate', {
      method: 'POST',
      body: JSON.stringify({ course_id: courseId })
    });
  },
  generateReportAsync: function(courseId) {
    return apiFetch('/reports/generate', {
      method: 'POST',
      body: JSON.stringify({ course_id: courseId })
    });
  },
  getTaskStatus: function(taskId) {
    return apiFetch('/reports/task/' + taskId);
  },
  listReports: function() {
    return apiFetch('/reports');
  },
  downloadReport: function(reportId) {
    var token = getToken();
    var a = document.createElement('a');
    a.href = API_BASE + '/reports/' + reportId + '/download?token=' + token;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },
  getStudentReports: function(studentId, courseId) {
    return apiFetch('/reports/student/' + studentId + (courseId ? '?course_id=' + courseId : ''));
  },

  // ---- 实训考核 ----
  getAssessment: function(studentId, courseId) {
    return apiFetch('/assessments/' + studentId + '/' + courseId);
  },

  // ---- 个人信息 ----
  getProfile: function() {
    return apiFetch('/auth/profile');
  },
  updateProfile: function(displayName) {
    return apiFetch('/auth/profile', {
      method: 'PUT',
      body: JSON.stringify({ display_name: displayName })
    });
  }
};

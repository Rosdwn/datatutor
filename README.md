# DataTutor —— 大数据技术学科垂类智能教学助手

面向一流学科建设的学科垂类大模型与创新应用开发平台。

## 已实现功能

| 功能 | 说明 |
|------|------|
| AI 实训副驾 | 终端内实时对话辅导（讯飞 Training Agent），对话自动保存供 Report Agent 使用 |
| AI 课程生成 | 讯飞Teacher Agent 自动生成实训课程和子任务 |
| Docker 隔离实训 | 每个学生独立容器，工具包宿主机挂载（镜像瘦身至229MB） |
| 多角色管理 | 学生/教师/管理员三端，课程班级子任务全生命周期 |
| 学情分析 | 进度追踪、任务完成率统计 |
| 实训计时 | 全程累计计时，后台 Persist，切换子任务不清零 |
| AI 实训报告生成 | 对话历史+学情分析多维度评估（讯飞 Report Agent） |
| AI 知识库问答 | 大数据领域知识 RAG 问答（讯飞 Knowledge Agent） |

## 技术栈

- 后端: Python 3.8+ / Flask / Flask-SocketIO / SQLAlchemy
- 数据库: MySQL 8.0 / Redis 7
- AI 引擎: 讯飞星辰 Agent 平台 4 个工作流 Agent + MaaS 兜底
- 容器化: Docker / Docker Compose
- 前端: HTML5 / Tailwind CSS / Iconify / Socket.IO

## 讯飞星火 Agent

| Agent | 输入 | 用途 |
|-------|------|------|
| 实训副驾 | 系统提示 + 终端上下文 + 文件预览 + 学生消息 | 终端内实时辅导，对话自动存储 |
| 知识问答 | 系统提示 + 终端上下文 + 学生问题 | 大数据领域知识检索 |
| 课程生成 | 课程主题 + 文件预览 | AI 自动生成实训课程和子任务 |
| 报告生成 | 课程信息 + 完成情况 + 对话摘要 | 自动生成学生个人实训报告 |

## 学生容器环境

学生登录后自动分配独立 Docker 实训环境：

- 容器名: dts-student{ID}
- SSH 端口: 2200+ID
- 账号: learner / 123456
- 课程数据: /home/learner/course-data/ (只读挂载)
- 安装包: /home/learner/packages/ (宿主机 /opt/packages 只读挂载)
- 镜像: 229MB（纯 Ubuntu + SSH + 基本工具，不含 JDK——运行时从挂载拷贝）

> **注意**：应用容器需挂载 `-v /var/run/docker.sock:/var/run/docker.sock` 才能创建学生容器。

10 类大数据组件安装包预置: Hadoop, Spark, Hive, Kafka, Flink, ZooKeeper, HBase, Flume, Sqoop, Storm

## 快速启动

环境要求: Docker & Docker Compose

```bash
git clone https://github.com/Rosdwn/datatutor.git
cd datatutor
# 将 backend/.env.example 复制为 backend/.env，填入讯飞 API 密钥
docker build -t datatutor-student -f Dockerfile_student .
docker compose up -d
```

访问: http://localhost（端口号80）


### 项目结构
```
datatutor/
├── backend/
│   ├── routes/         ai.py / auth.py / courses.py / chat.py / reports.py ...
│   ├── terminal_ws/    SSH WebSocket 终端
│   └── tasks/          报告生成（同步）
├── frontend/
│   ├── static/
│   │   └── js/
│   │       └── api.js  前端 API 封装层
│   ├── login.html      登录/注册
│   ├── hub.html        学生选课
│   ├── main.html       学生实训终端
│   ├── teacher.html    教师工作台
│   └── profile.html    个人中心
├── Dockerfile_student  学生容器镜像
├── docker-compose.yml
└── .env.example
```


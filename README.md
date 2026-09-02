# 人脸情绪识别系统（基于 RAF-DB）

<p align="center">
  <img src="frontend/public/logo.png" alt="Facial Emotion Recognition Logo" width="160" />
</p>

<p align="center">
  <img alt="Python 3.8" src="https://img.shields.io/badge/Python-3.8-blue" />
  <img alt="Flask" src="https://img.shields.io/badge/Flask-2.x-00A6D6" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3.x-41B883" />
  <img alt="TensorFlow" src="https://img.shields.io/badge/TensorFlow-Keras-FF6F00" />
  <img alt="License: CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey" />
</p>

<p align="center">
  <strong>基于 RAF-DB 的人脸情绪识别系统</strong><br />
  支持图片识别、视频分析、历史记录与心理健康辅助功能
</p>

---

## 简介

本项目面向学习与研究场景，完整覆盖人脸情绪识别的常见链路：**模型训练 → 权重导出 → 图像预处理 → 多模型推理 → 结果展示与用户侧能力**。后端基于 Flask 与 TensorFlow / Keras，前端基于 Vue 3 与 Vite。

主要能力包括：

- 单图 / 批量图片情绪识别
- 视频抽帧情绪分析
- 人脸检测、对齐与质量评估
- 多模型切换：CNN、VGG、SE-Net
- JWT 认证、用户管理与管理后台
- 情绪汇总、日记、感恩记录与健康评估等辅助模块

> **许可说明**：本项目采用 [CC BY-NC 4.0](LICENSE)。仅供学习、研究与个人技术交流；禁止商业用途（含出售、商业部署、收费服务等）。复用时请保留署名并遵守非商用约定。

> **权重说明**：本仓库**不包含预训练模型权重**（文件体积大，且 RAF-DB 需自行申请）。克隆后可直接**预览 UI 与登录等功能**；**图片 / 视频情绪识别**需先按 [training/README.md](training/README.md) 训练并导出权重到 `models/`。

---

## 功能特性

| 模块 | 说明 |
|------|------|
| 情绪识别 | 单图预测、批量预测、视频抽帧分析、多模型切换 |
| 图像处理 | MTCNN 人脸检测与对齐、Haar 回退、清晰度 / 亮度 / 对比度评估 |
| 用户与管理 | JWT 认证、注册登录、资料管理、管理后台、历史统计 |
| 心理健康辅助 | 情绪汇总、健康评估、情绪日记、感恩记录 |

> 本系统输出仅供参考，不构成医学或心理诊断建议。

---

## 技术栈

- **前端**：Vue 3、Vite、Pinia、Vue Router、Element Plus、Axios、ECharts
- **后端**：Python、Flask、Flask-CORS、Flask-SQLAlchemy、OpenCV、Pillow、PyJWT
- **深度学习**：TensorFlow / Keras
- **数据存储**：默认 SQLite（可通过 `DATABASE_URL` 切换）

---

## 仓库结构

```text
.
├── backend/                 # Flask API
│   ├── main.py              # 启动入口
│   ├── src/                 # 运行时代码（api / auth / config / storage / ml）
│   ├── scripts/             # 迁移与运维脚本
│   ├── tests/
│   └── data/                # 运行时数据（uploads / logs / db，内容默认忽略）
├── frontend/                # Vue 3 前端
│   ├── public/              # Favicon、Logo 等静态资源
│   └── src/                 # pages / api / assets / stores ...
├── training/                # RAF-DB 训练 Notebook 与说明
│   ├── notebooks/           # CNN / VGG / SE 训练脚本
│   └── README.md
├── models/                  # 模型权重（大文件，默认忽略）
├── docker-compose.yml       # Docker 一键部署
├── .env.example             # Docker 环境变量模板
├── docs/                    # 目录约定与设计源文件
├── LICENSE
└── README.md
```

详细约定见 [docs/directory-structure.md](docs/directory-structure.md)，后端说明见 [backend/README.md](backend/README.md)。

---

## 快速开始

### 环境要求

- **Python 3.8**（本项目在本地开发与联调时使用并验证的版本）
- Node.js 18+（建议，仅前端需要）
- TensorFlow 2.10.x（推理端需单独安装；**仅预览 UI 时可暂不安装**，见下方「无权重 UI 预览」）
- 模型权重（`models/`，**完整识别功能才需要**；需自行训练，见 [training/README.md](training/README.md)）

### 使用方式一览

本仓库支持三种使用深度，可按需选择：

| 模式 | 需要 `models/` | 需要后端 | 需要 TensorFlow | 可用能力 |
|------|------------------|----------|-----------------|----------|
| **A. 仅 UI 预览** | 否 | 否 | 否 | 浏览登录页与前端界面；**无法登录** |
| **B. UI + 业务（无识别）** | 否 | 是 | 是* | 登录、注册、管理后台、个人中心、各页面导航；历史 / 统计为空；**识别不可用** |
| **C. 完整功能** | 是 | 是 | 是 | 上述全部 + 图片 / 批量 / 视频情绪识别 |

\* 模式 B 下后端会启动，但缺少权重时模型预热会跳过；`/api/health` 仍返回正常，`/api/models` 显示各模型 `available: false`。

**模式 B 可用 / 不可用对照：**

| 功能 | 无权重 | 有权重 |
|------|--------|--------|
| 登录 / 注册 / JWT | ✅ | ✅ |
| 首页、关于、个人中心 | ✅ | ✅ |
| 管理后台（用户管理等） | ✅ | ✅ |
| 历史记录、数据分析、心理健康页面 | ✅（多为空数据） | ✅ |
| 单图 / 批量图片识别 | ❌ | ✅ |
| 视频情绪分析 | ❌ | ✅ |
| 多模型切换（CNN / VGG / SE） | ❌ | ✅ |

---

### 1. 克隆

```bash
git clone https://github.com/LinJJ12/RAF-DB-Based-Facial-Emotion-Recognition-System.git
cd RAF-DB-Based-Facial-Emotion-Recognition-System
```

### 无权重 UI 预览（模式 A · 最快）

仅查看前端界面，**不需要** Python、TensorFlow 与模型权重。

```cmd
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:3000`，可浏览登录页与页面布局；**未启动后端时无法登录**。

### UI + 登录，暂不做识别（模式 B）

适合想体验完整界面、账号体系与管理后台，但**尚未训练模型**的用户。按下方 **§2 后端** 与 **§3 前端** 启动即可（`models/` 可为空）。

使用演示账号登录：`admin` / `admin123`（管理员）、`test` / `test123`（普通用户）。  
可浏览各页面；在「图片识别」「视频分析」中上传并识别时会报错，属预期行为。  
访问 `http://localhost:5000/api/models` 可确认各模型均为 `available: false`。

### 2. 后端（模式 B / C 通用）

```cmd
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install "tensorflow==2.10.1"
python main.py
```

默认地址：`http://localhost:5000`

也可从仓库根目录执行：`python backend/main.py`。

> **无权重时**：后端仍会启动；TensorFlow 需安装，但缺失的模型文件会在预热阶段跳过，识别 API 不可用。

> **数据库**：首次启动会自动在 `backend/data/db/emotion_recognition.db` 创建 SQLite 及全部表，并写入演示账号 `admin/admin123`、`test/test123`。无需手动迁移。详见 [backend/README.md](backend/README.md)。

### 3. 前端（模式 B / C · 本地开发）

```cmd
cd frontend
npm install
npm run dev
```

默认地址：`http://localhost:3000`（开发服务器将 `/api` 代理至后端）。

### 完整功能：训练模型（模式 C）

情绪识别依赖 `models/` 下的权重文件。请按 [training/README.md](training/README.md) 完成 RAF-DB 训练并导出，再重启后端。  
至少准备 `RAF_CNN_83_best_model.h5` 即可体验单图识别；四模型齐备后可切换 CNN / VGG / SE。

---

## Docker 部署

无需本地安装 Python / Node，使用 Docker Compose 一键启动前后端。

### 前置条件

- [Docker](https://docs.docker.com/get-docker/) 与 [Docker Compose](https://docs.docker.com/compose/) v2+
- **模型权重（可选）**：`models/` 为空时可正常启动并预览 UI、登录；**情绪识别**需先训练权重并放入 `models/`（见 [training/README.md](training/README.md)）

### 启动步骤

```bash
# 1. 克隆仓库
git clone https://github.com/LinJJ12/RAF-DB-Based-Facial-Emotion-Recognition-System.git
cd RAF-DB-Based-Facial-Emotion-Recognition-System

# 2. （可选）训练模型后放入 models/ — 仅完整识别功能需要

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，设置 JWT_SECRET_KEY

# 4. 构建并启动
docker compose up -d --build
```

浏览器访问：**http://localhost:8080**

演示账号：`admin` / `admin123`，`test` / `test123`

> **无权重时**：容器可正常启动，可登录并浏览各页面；识别相关接口不可用。日志中会出现模型加载跳过/失败的警告，可忽略直至放入权重。

### 常用命令

```bash
docker compose ps          # 查看状态
docker compose logs -f     # 查看日志
docker compose down        # 停止并移除容器
docker compose down -v     # 同时删除数据库卷（清空用户数据）
```

### 容器说明

| 服务 | 说明 | 端口 |
|------|------|------|
| `frontend` | Nginx 托管 Vue 静态页，`/api` 反向代理至后端 | 8080 → 80 |
| `backend` | Python 3.8 + TensorFlow 2.10.1 + Gunicorn | 内部 5000 |

持久化数据保存在 Docker 卷 `backend-data`（SQLite、上传文件、日志）。模型通过 `./models` 目录只读挂载。

> 首次启动会预热模型，可能需要 1–3 分钟；可通过 `docker compose logs -f backend` 查看进度。

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `JWT_SECRET_KEY` | JWT 签名密钥。**部署前必须设置为足够随机的强密钥** |
| `DATABASE_URL` | 可选。默认：`backend/data/db/emotion_recognition.db` |

---

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 服务健康检查 |
| `GET` | `/api/models` | 模型状态 |
| `POST` | `/api/predict` | 单图情绪识别 |
| `POST` | `/api/batch_predict` | 批量识别 |
| `POST` | `/api/video/upload` | 上传视频 |
| `POST` | `/api/video/analyze` | 视频情绪分析 |

示例：

```bash
curl -X POST http://localhost:5000/api/predict \
  -H "Content-Type: application/json" \
  -d "{\"image\":\"data:image/jpeg;base64,...\",\"model\":\"cnn\",\"detect_face\":true}"
```

---

## 模型说明

支持的模型键名：`cnn`、`vgg`、`se81`、`se83`。  
权重与 SavedModel 放置于 `models/`，由配置中的 `MODEL_PATHS` 加载。

**本仓库不包含预训练权重**（文件体积大，且 RAF-DB 需自行申请）。  
无权重时可正常使用 UI 与账号相关功能；**图片 / 视频情绪识别**须完成下方训练流程。

### 获取模型权重（完整识别功能）

请按 [training/README.md](training/README.md) **自行训练**：

1. 申请 / 下载 [RAF-DB](http://www.whdeng.cn/RAF/model1.html) 数据集  
2. 运行 `training/notebooks/` 下对应 Notebook（CNN / VGG / SE）  
3. 将训练产物复制并重命名到仓库根目录 `models/`（文件名见 `backend/src/config/settings.py`）

完成后重启后端，访问 `/api/models` 确认各模型 `available: true`。

---

## 安全与隐私

- 上传图片 / 视频默认保存在本地 `backend/data/`，请勿将含真实用户数据的数据库、日志或上传目录提交到公开仓库。
- 公网或共享环境部署前，务必配置 `JWT_SECRET_KEY`，并修改或禁用本地演示用账号与弱口令。
- 本仓库文档与示例不包含真实用户隐私数据；请勿在 Issue、截图或提交中粘贴个人身份信息、密钥或生产凭据。

---

## 部署注意

- 推荐使用 **Python 3.8** 与 **TensorFlow 2.10.x**（与训练 Notebook 环境一致）；GPU 需匹配对应 CUDA / cuDNN。
- **Docker 部署**见上方 [Docker 部署](#docker-部署推荐用于快速体验) 一节；生产环境请修改 `.env` 中的 `JWT_SECRET_KEY` 并关闭演示弱口令。
- 非 Docker 场景下，生产环境建议使用 Gunicorn / uWSGI 等 WSGI 服务器，并关闭调试模式。
- 日志、上传缓存与数据库文件应继续保持在 `.gitignore` 中。

---

## 许可

详见 [LICENSE](LICENSE)（CC BY-NC 4.0）。

---

## 致谢与参考

- [RAF-DB](http://www.whdeng.cn/RAF/model1.html) 表情识别数据集及相关研究工作

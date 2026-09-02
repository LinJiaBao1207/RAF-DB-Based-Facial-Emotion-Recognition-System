# 后端说明

基于 RAF-DB 的人脸情绪识别 Flask API。

## 目录

```text
backend/
├── main.py                 # 启动：python main.py
├── requirements.txt
├── src/
│   ├── api/                # HTTP（app 路由、health 蓝图）
│   ├── auth/               # JWT 认证
│   ├── config/             # settings（路径、模型、服务）
│   ├── storage/            # SQLAlchemy 模型与初始化
│   └── ml/                 # 预处理、人脸质量、视频处理
├── scripts/                # 一次性迁移 / 运维脚本
├── tests/
└── data/                   # 运行时 uploads、logs、sqlite（内容默认忽略）
```

## 启动

### 环境要求

- **Python 3.8**（本地跑通并验证的版本）
- **TensorFlow 2.10.x**（需单独安装，见下方命令）

```cmd
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install "tensorflow==2.10.1"
python main.py
```

服务地址：`http://localhost:5000`

也可从仓库根目录执行：`python backend/main.py`。

> **无模型权重时**：服务仍可启动；模型预热会跳过缺失文件，识别 API 不可用，其余接口（登录、管理、健康检查等）正常。详见根目录 [README](../README.md#使用方式一览)。

### 数据库（自动初始化）

克隆仓库后**无需手动建库**。首次启动（或运行初始化脚本）时会自动：

1. 创建 `backend/data/db/` 目录
2. 生成 SQLite 文件 `emotion_recognition.db`
3. 创建全部业务表（用户、识别历史、情绪汇总、健康评估等）
4. 写入演示账号（若不存在）：
   - `admin` / `admin123`（管理员）
   - `test` / `test123`（普通用户）

仅初始化数据库、不启动推理服务时：

```cmd
cd backend\scripts
python init_database.py
```

`scripts/migrate_database.py` 等迁移脚本**仅用于从旧版本升级**已有数据库；全新克隆一般不需要运行。

## Docker 部署

仓库根目录提供 `docker-compose.yml`，详见 [README](../README.md#docker-部署推荐用于快速体验)。

```bash
cp .env.example .env
docker compose up -d --build
```

后端镜像：`backend/Dockerfile`（Python 3.8 + TensorFlow 2.10.1 + Gunicorn）。

## 安全配置

- 通过环境变量设置 `JWT_SECRET_KEY`（强随机值）。未设置时仅适合本地调试。
- `data/` 下的数据库、上传文件与日志可能包含用户数据，**请勿提交到公开仓库**。
- 本地开发若存在演示账号，公网部署前必须修改口令或删除演示用户。

## 运维脚本

脚本位于 `scripts/`：

```cmd
cd backend\scripts
python migrate_database.py
```

新脚本推荐写法：

```python
import _bootstrap  # noqa: F401
from src.storage.database import db
from src.api.app import app
```

SQLite 路径解析见 `_db_path.py`（优先 `data/db/`，兼容旧 `instance/`）。

## 测试

```cmd
cd backend
python tests\test_upload_paths.py
python tests\test_preprocessing.py
```

依赖真实模型文件的用例需先将权重放到仓库根目录 `models/`。

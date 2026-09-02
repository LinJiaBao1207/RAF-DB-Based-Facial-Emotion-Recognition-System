# 目录结构说明

本仓库采用「运行时代码 / 运维脚本 / 文档 / 品牌资源」分离的组织方式，便于维护与二次开发。

启动与使用说明见根目录 [README.md](../README.md) 与 [backend/README.md](../backend/README.md)。

## 仓库布局

```text
基于RAF-DB的人脸情绪识别系统/
├── README.md / LICENSE / .gitignore
├── docker-compose.yml                 # Docker Compose 编排
├── .env.example                       # Docker 环境变量模板
├── docs/                              # 文档与设计稿（非运行时）
│   ├── directory-structure.md
│   └── brand/logo-design-source.png   # Logo 设计源图
├── training/                          # 模型训练（Notebook + 说明）
│   ├── README.md
│   ├── requirements.txt
│   └── notebooks/                     # RAF_CNN / RAF_VGG / RAF_SE
├── models/                            # 推理权重（通常 gitignore）
├── backend/
│   ├── Dockerfile                     # 推理后端镜像
│   ├── docker-entrypoint.sh           # 模型预热 + Gunicorn 启动
│   ├── main.py                        # 启动入口
│   ├── requirements.txt
│   ├── README.md
│   ├── src/
│   │   ├── api/                       # Flask HTTP（app、health）
│   │   ├── auth/                      # JWT 认证
│   │   ├── config/settings.py         # 路径 / 模型 / 服务配置（单一来源）
│   │   ├── storage/                   # SQLAlchemy
│   │   └── ml/                        # 预处理、人脸质量、视频
│   ├── scripts/                       # 迁移与运维一次性脚本
│   ├── tests/
│   └── data/                          # 运行时 uploads / logs / db（内容忽略）
└── frontend/
    ├── Dockerfile                     # 前端构建 + Nginx
    ├── nginx.conf                     # 静态资源与 /api 反代
    ├── public/                        # favicon.png、logo.png；演示视频（忽略）
    ├── index.html / package.json / vite.config.js
    └── src/
        ├── pages/                     # 路由页面
        ├── api/                       # HTTP 客户端（client / health）
        ├── utils/                     # IndexedDB 等非 HTTP 工具
        ├── components/ / stores/ / router/ / data/
        └── assets/
            ├── brand/logo.png         # 页面内引用的品牌图
            └── styles/theme.css
```

## 品牌资源

| 路径 | 用途 |
|------|------|
| `frontend/src/assets/brand/` | 前端组件通过 `@/assets/brand/logo.png` 引用 |
| `frontend/public/` | Favicon、README 展示图、静态 URL |
| `docs/brand/` | 设计源文件，不参与前端打包 |

请勿在仓库根目录新建 `assets/` 存放应用图标，以免与前端静态资源职责混淆。

## 训练与推理分工

| 目录 | 职责 |
|------|------|
| `training/` | RAF-DB 数据集上的模型训练与导出说明 |
| `models/` | 训练产出的 `.h5` / SavedModel，供 `backend` 加载推理 |
| `backend/src/ml/` | 运行时预处理、人脸质量、视频抽帧（不含训练逻辑） |

训练流程详见 [training/README.md](../training/README.md)。

## 约定

- 后端在 `backend/` 下运行：`python main.py`；业务导入形如 `from src.api.app import app`
- 配置只读 `src.config.settings`（含 `MODEL_PATHS`、`UPLOAD_FOLDER`、`DATABASE_URI`）
- 上传文件相对 `backend/data/uploads/` 存库，经 `/api/uploads/<path>` 访问
- 运维脚本：`import _bootstrap` 后再 `from src....`
- 前端 HTTP 客户端位于 `src/api/`；路由页面位于 `src/pages/`

## 请勿错放

- 迁移 / 修复脚本应放在 `backend/scripts/`，不要放回 `backend/` 根目录
- 页面组件使用 `pages/`，不要恢复已废弃的 `views/` 目录命名
- Axios 封装放在 `api/`，不要放回 `utils/`

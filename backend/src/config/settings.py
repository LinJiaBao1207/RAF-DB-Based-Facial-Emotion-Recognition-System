"""
配置文件 — 路径与运行参数的单一来源
"""
import os
from pathlib import Path

# backend/src/config -> backend/src -> backend -> project root
CONFIG_DIR = Path(__file__).resolve().parent
SRC_DIR = CONFIG_DIR.parent
BACKEND_DIR = SRC_DIR.parent
PROJECT_DIR = BACKEND_DIR.parent

# 运行时数据根（uploads / logs / db）
DATA_DIR = BACKEND_DIR / 'data'
UPLOAD_FOLDER = DATA_DIR / 'uploads'
LOG_DIR = DATA_DIR / 'logs'
DB_DIR = DATA_DIR / 'db'
SQLITE_PATH = DB_DIR / 'emotion_recognition.db'

# 模型目录（仓库根下）
MODEL_DIR = PROJECT_DIR / 'models'
MODEL_CONFIG = {
    'cnn': {
        'path': MODEL_DIR / 'RAF_CNN_83_best_model.h5',
        'input_shape': (100, 100, 3),
        'description': 'CNN基础模型',
        'accuracy': 0.8377
    },
    'vgg': {
        'path': MODEL_DIR / 'RAF_VGG_80_best_model.h5',
        'input_shape': (100, 100, 3),
        'description': 'VGG16迁移学习模型',
        'accuracy': 0.8000
    },
    'se81': {
        'path': MODEL_DIR / 'RAF_SE_81_saved_model',
        'input_shape': (100, 100, 3),
        'description': 'SE注意力机制模型',
        'accuracy': 0.8100
    },
    'se83': {
        'path': MODEL_DIR / 'RAF_SE_83_saved_model',
        'input_shape': (100, 100, 3),
        'description': 'SE注意力机制模型(最佳)',
        'accuracy': 0.8300
    }
}

# 对外推理路径（字符串，供 load / 状态接口使用）
MODEL_PATHS = {name: str(cfg['path']) for name, cfg in MODEL_CONFIG.items()}

# 服务器配置
HOST = '0.0.0.0'
PORT = 5000
DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')

# 上传配置
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB

# 数据库配置
DATABASE_URI = os.environ.get(
    'DATABASE_URL',
    f'sqlite:///{SQLITE_PATH.as_posix()}'
)
DATABASE_CONFIG = {
    'type': 'sqlite',
    'sqlite_path': SQLITE_PATH,
    # 可选 MySQL 示例（请通过环境变量配置真实凭据，勿将生产密码写入仓库）
    'mysql': {
        'host': os.environ.get('MYSQL_HOST', 'localhost'),
        'port': int(os.environ.get('MYSQL_PORT', '3306')),
        'user': os.environ.get('MYSQL_USER', 'root'),
        'password': os.environ.get('MYSQL_PASSWORD', ''),
        'database': os.environ.get('MYSQL_DATABASE', 'emotion_db')
    }
}

# 日志配置
LOG_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': LOG_DIR / 'app.log'
}

# 确保目录存在
for _dir in (UPLOAD_FOLDER, LOG_DIR, DB_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# 弱默认密钥提醒（生产务必通过环境变量覆盖）
if os.environ.get('JWT_SECRET_KEY') in (None, '', 'your-secret-key-change-in-production'):
    import warnings
    warnings.warn(
        'JWT_SECRET_KEY 未设置或仍为默认值，生产环境请通过环境变量配置强随机密钥。',
        UserWarning,
        stacklevel=1,
    )

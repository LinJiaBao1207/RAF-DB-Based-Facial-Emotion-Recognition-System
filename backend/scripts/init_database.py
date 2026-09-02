"""
初始化 SQLite 数据库（无需启动 Flask / 加载 TensorFlow）。

用法:
    cd backend/scripts
    python init_database.py
"""
import _bootstrap  # noqa: F401

from flask import Flask

from src.config.settings import DATABASE_URI, SQLITE_PATH
from src.storage.database import init_db, db, User


def main():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    init_db(app)

    with app.app_context():
        user_count = User.query.count()
        print(f'数据库路径: {SQLITE_PATH}')
        print(f'users 表记录数: {user_count}')
        if user_count:
            for u in User.query.order_by(User.id).all():
                print(f'  - {u.username} ({u.role})')
        print('完成。可直接运行 python main.py 启动服务。')


if __name__ == '__main__':
    main()

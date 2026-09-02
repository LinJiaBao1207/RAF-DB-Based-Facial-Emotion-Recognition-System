"""
用户认证模块
提供登录、注册、token管理等认证功能
"""

from flask import Blueprint, request, jsonify, current_app
from functools import wraps
import jwt
import hashlib
import secrets
from datetime import datetime, timedelta
import os

# 尝试导入数据库模型（可选）
try:
    from src.storage.database import db, User
    HAVE_DB = True
except Exception:
    HAVE_DB = False
# 创建认证蓝图
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# JWT配置
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24
REFRESH_TOKEN_EXPIRATION_DAYS = 7

# 模拟用户数据库（实际项目中应使用真实数据库）
USERS_DB = {
    # 默认测试用户
    'admin': {
        'id': 1,
        'username': 'admin',
        'email': 'admin@emotion-ai.com',
        'password_hash': hashlib.sha256('admin123'.encode()).hexdigest(),
        'role': 'admin',
        'avatar': '',
        'created_at': '2025-01-01T00:00:00Z',
        'is_active': True,
        'is_verified': True,
        'permissions': ['user_management', 'system_settings', 'data_export', 'analytics']
    },
    'test': {
        'id': 2,
        'username': 'test',
        'email': 'test@emotion-ai.com',
        'password_hash': hashlib.sha256('test123'.encode()).hexdigest(),
        'role': 'user',
        'avatar': '',
        'created_at': '2025-01-01T00:00:00Z',
        'is_active': True,
        'is_verified': True,
        'permissions': ['basic_emotion_recognition', 'personal_data']
    }
}

# 模拟刷新token存储（实际项目中应使用Redis或数据库）
REFRESH_TOKENS = {}

def hash_password(password):
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    """验证密码"""
    return hash_password(password) == password_hash

def generate_tokens(user_id):
    """生成访问token和刷新token"""
    # 访问token
    access_payload = {
        'user_id': user_id,
        'type': 'access',
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    access_token = jwt.encode(access_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    # 刷新token
    refresh_token = secrets.token_urlsafe(32)
    
    # 存储刷新token
    REFRESH_TOKENS[refresh_token] = {
        'user_id': user_id,
        'expires_at': datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS),
        'is_used': False
    }
    
    return access_token, refresh_token

def verify_token(token):
    """验证token"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _user_dict_from_payload(payload):
    """从 JWT payload 加载用户 dict。"""
    if not payload or payload.get('type') != 'access':
        return None
    user = get_user_by_id(payload['user_id'])
    if not user or not user.get('is_active'):
        return None
    return user


def _sync_user_to_db(user_dict, **fields):
    """将用户字段变更持久化到数据库。"""
    if not HAVE_DB or not user_dict.get('id'):
        return
    u = User.query.get(user_dict['id'])
    if not u:
        return
    for key, value in fields.items():
        if hasattr(u, key):
            setattr(u, key, value)
    db.session.commit()


def _sync_user_to_memory(user_dict, **fields):
    """同步内存 USERS_DB（兼容旧逻辑）。"""
    username = user_dict.get('username')
    if username and username in USERS_DB:
        USERS_DB[username].update(fields)

def get_user_by_id(user_id):
    """根据ID获取用户"""
    # 优先从数据库读取（如果可用）
    if HAVE_DB:
        u = User.query.get(user_id)
        if u:
            return {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'password_hash': u.password_hash,
                'role': u.role,
                'avatar': u.avatar,
                'created_at': u.created_at.isoformat() if u.created_at else None,
                'is_active': u.is_active,
                'is_verified': u.is_verified
            }
    # 回退到内存数据库
    for username, user_data in USERS_DB.items():
        if user_data['id'] == user_id:
            return user_data
    return None

def get_user_by_username(username):
    """根据用户名获取用户"""
    if HAVE_DB:
        u = User.query.filter_by(username=username).first()
        if u:
            return {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'password_hash': u.password_hash,
                'role': u.role,
                'avatar': u.avatar,
                'created_at': u.created_at.isoformat() if u.created_at else None,
                'is_active': u.is_active,
                'is_verified': u.is_verified
            }
    return USERS_DB.get(username)

def get_user_by_email(email):
    """根据邮箱获取用户"""
    if HAVE_DB:
        u = User.query.filter_by(email=email).first()
        if u:
            return {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'password_hash': u.password_hash,
                'role': u.role,
                'avatar': u.avatar,
                'created_at': u.created_at.isoformat() if u.created_at else None,
                'is_active': u.is_active,
                'is_verified': u.is_verified
            }
    for user_data in USERS_DB.values():
        if user_data['email'] == email:
            return user_data
    return None

def token_required(f):
    """token验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # 从请求头获取token
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(' ')[1]  # Bearer <token>
            except IndexError:
                return jsonify({'error': 'Invalid token format'}), 401
        
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        
        user = _user_dict_from_payload(verify_token(token))
        if not user:
            return jsonify({'error': 'Invalid token'}), 401
        
        # 将用户信息添加到请求上下文
        request.current_user = user
        return f(*args, **kwargs)
    
    return decorated


def token_required_or_query(f):
    """GET 资源（如 img）可用 Header 或 ?token= 传递 JWT。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            parts = request.headers['Authorization'].split(' ', 1)
            if len(parts) == 2:
                token = parts[1]
        if not token:
            token = request.args.get('token')
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        user = _user_dict_from_payload(verify_token(token))
        if not user:
            return jsonify({'error': 'Invalid token'}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated

@auth_bp.route('/register', methods=['POST'])
def register():
    """用户注册"""
    try:
        data = request.get_json()
        
        # 验证必需字段
        required_fields = ['username', 'email', 'password']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing field: {field}'}), 400
        
        username = data['username'].lower().strip()
        email = data['email'].lower().strip()
        password = data['password']
        
        # 验证用户名格式
        if len(username) < 3 or len(username) > 20:
            return jsonify({'error': 'Username must be 3-20 characters long'}), 400
        
        if not username.replace('_', '').isalnum():
            return jsonify({'error': 'Username can only contain letters, numbers, and underscores'}), 400
        
        # 验证邮箱格式
        if '@' not in email or '.' not in email:
            return jsonify({'error': 'Invalid email format'}), 400
        
        # 验证密码强度
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters long'}), 400
        
        # 检查用户是否已存在
        if get_user_by_username(username):
            return jsonify({'error': 'Username already exists'}), 409
        
        if get_user_by_email(email):
            return jsonify({'error': 'Email already exists'}), 409
        
        # 创建新用户
        user_id = max([user['id'] for user in USERS_DB.values()]) + 1
        new_user = {
            'id': user_id,
            'username': username,
            'email': email,
            'password_hash': hash_password(password),
            'role': 'user',
            'avatar': '',
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'is_active': True,
            'is_verified': False  # 需要邮箱验证
        }
        
        USERS_DB[username] = new_user

        # 如果数据库可用，写入数据库以持久化用户
        if HAVE_DB:
            try:
                if not User.query.filter((User.username==username)|(User.email==email)).first():
                    db_user = User(
                        username=username,
                        email=email,
                        password_hash=new_user['password_hash'],
                        role='user',
                        avatar='',
                        is_active=True,
                        is_verified=False
                    )
                    db.session.add(db_user)
                    db.session.commit()
                    # 同步生成的 id
                    new_user['id'] = db_user.id
            except Exception as e:
                # 记录但不阻止注册（兼容旧逻辑）
                print(f"Warning: failed to persist new user to DB: {e}")
        
        # 生成token
        access_token, refresh_token = generate_tokens(user_id)
        
        return jsonify({
            'message': 'User registered successfully',
            'user': {
                'id': new_user['id'],
                'username': new_user['username'],
                'email': new_user['email'],
                'role': new_user['role'],
                'avatar': new_user['avatar'],
                'created_at': new_user['created_at'],
                'is_verified': new_user['is_verified']
            },
            'token': access_token,
            'refreshToken': refresh_token
        }), 201
        
    except Exception as e:
        return jsonify({'error': f'Registration failed: {str(e)}'}), 500

@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    try:
        data = request.get_json()
        
        if not data.get('username') or not data.get('password'):
            return jsonify({'error': 'Username and password are required'}), 400
        
        username = data['username'].lower().strip()
        password = data['password']
        
        # 查找用户（支持用户名或邮箱登录）
        user = get_user_by_username(username)
        if not user:
            user = get_user_by_email(username)
        
        if not user:
            return jsonify({'error': 'Invalid username or password'}), 401
        
        # 验证密码
        if not verify_password(password, user['password_hash']):
            return jsonify({'error': 'Invalid username or password'}), 401
        
        # 检查用户状态
        if not user['is_active']:
            return jsonify({'error': 'Account is deactivated'}), 401
        
        # 生成token
        access_token, refresh_token = generate_tokens(user['id'])
        
        return jsonify({
            'message': 'Login successful',
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'role': user['role'],
                'avatar': user['avatar'],
                'created_at': user['created_at'],
                'is_verified': user['is_verified']
            },
            'token': access_token,
            'refreshToken': refresh_token
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Login failed: {str(e)}'}), 500

@auth_bp.route('/logout', methods=['POST'])
@token_required
def logout():
    """用户登出"""
    try:
        # 这里可以添加token黑名单逻辑
        return jsonify({'message': 'Logout successful'}), 200
    except Exception as e:
        return jsonify({'error': f'Logout failed: {str(e)}'}), 500

@auth_bp.route('/refresh', methods=['POST'])
def refresh_token():
    """刷新访问token"""
    try:
        data = request.get_json()
        refresh_token = data.get('refreshToken')
        
        if not refresh_token:
            return jsonify({'error': 'Refresh token is required'}), 400
        
        # 验证刷新token
        if refresh_token not in REFRESH_TOKENS:
            return jsonify({'error': 'Invalid refresh token'}), 401
        
        token_data = REFRESH_TOKENS[refresh_token]
        
        # 检查token是否过期
        if datetime.utcnow() > token_data['expires_at']:
            del REFRESH_TOKENS[refresh_token]
            return jsonify({'error': 'Refresh token expired'}), 401
        
        # 检查token是否已使用
        if token_data['is_used']:
            del REFRESH_TOKENS[refresh_token]
            return jsonify({'error': 'Refresh token already used'}), 401
        
        # 标记token为已使用
        token_data['is_used'] = True
        
        # 生成新的token
        user_id = token_data['user_id']
        access_token, new_refresh_token = generate_tokens(user_id)
        
        return jsonify({
            'token': access_token,
            'refreshToken': new_refresh_token
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Token refresh failed: {str(e)}'}), 500

@auth_bp.route('/me', methods=['GET'])
@token_required
def get_current_user():
    """获取当前用户信息"""
    try:
        user = request.current_user
        return jsonify({
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'role': user['role'],
                'avatar': user['avatar'],
                'created_at': user['created_at'],
                'is_verified': user['is_verified']
            }
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to get user info: {str(e)}'}), 500

@auth_bp.route('/profile', methods=['PUT'])
@token_required
def update_profile():
    """更新用户资料"""
    try:
        user = request.current_user
        data = request.get_json()
        
        # 允许更新的字段
        updatable_fields = ['email', 'avatar']
        updates = {}
        for field in updatable_fields:
            if field in data:
                user[field] = data[field]
                updates[field] = data[field]
        if updates:
            _sync_user_to_db(user, **updates)
            _sync_user_to_memory(user, **updates)
        
        return jsonify({
            'message': 'Profile updated successfully',
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'role': user['role'],
                'avatar': user['avatar'],
                'created_at': user['created_at'],
                'is_verified': user['is_verified']
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to update profile: {str(e)}'}), 500

@auth_bp.route('/change-password', methods=['POST'])
@token_required
def change_password():
    """修改密码"""
    try:
        user = request.current_user
        data = request.get_json()
        
        old_password = data.get('oldPassword')
        new_password = data.get('newPassword')
        
        if not old_password or not new_password:
            return jsonify({'error': 'Old password and new password are required'}), 400
        
        # 验证旧密码
        if not verify_password(old_password, user['password_hash']):
            return jsonify({'error': 'Invalid old password'}), 400
        
        # 验证新密码
        if len(new_password) < 6:
            return jsonify({'error': 'New password must be at least 6 characters long'}), 400
        
        # 更新密码
        new_hash = hash_password(new_password)
        user['password_hash'] = new_hash
        _sync_user_to_db(user, password_hash=new_hash)
        _sync_user_to_memory(user, password_hash=new_hash)
        
        return jsonify({'message': 'Password changed successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to change password: {str(e)}'}), 500

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """忘记密码"""
    try:
        data = request.get_json()
        email = data.get('email')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        user = get_user_by_email(email)
        if user:
            # 这里应该发送重置密码邮件
            # 为了演示，我们只返回成功消息
            pass
        
        # 无论用户是否存在，都返回成功消息（防止邮箱枚举攻击）
        return jsonify({'message': 'If the email exists, a reset link has been sent'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to process forgot password: {str(e)}'}), 500

@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """重置密码"""
    try:
        data = request.get_json()
        token = data.get('token')
        new_password = data.get('newPassword')
        
        if not token or not new_password:
            return jsonify({'error': 'Token and new password are required'}), 400
        
        # 这里应该验证重置token的有效性
        # 为了演示，我们只返回成功消息
        return jsonify({'message': 'Password reset successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to reset password: {str(e)}'}), 500

@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    """验证邮箱"""
    try:
        data = request.get_json()
        token = data.get('token')
        
        if not token:
            return jsonify({'error': 'Token is required'}), 400
        
        # 这里应该验证邮箱验证token的有效性
        # 为了演示，我们只返回成功消息
        return jsonify({'message': 'Email verified successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to verify email: {str(e)}'}), 500

@auth_bp.route('/resend-verification', methods=['POST'])
@token_required
def resend_verification():
    """重新发送验证邮件"""
    try:
        user = request.current_user
        
        if user['is_verified']:
            return jsonify({'error': 'Email is already verified'}), 400
        
        # 这里应该发送验证邮件
        return jsonify({'message': 'Verification email sent'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to resend verification: {str(e)}'}), 500

@auth_bp.route('/stats', methods=['GET'])
@token_required
def get_user_stats():
    """获取用户统计信息"""
    try:
        user = request.current_user
        
        # 这里应该从数据库获取真实的统计信息
        stats = {
            'total_predictions': 0,
            'active_days': 0,
            'favorite_emotion': 'happy',
            'last_login': datetime.utcnow().isoformat() + 'Z'
        }
        
        return jsonify({'stats': stats}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to get stats: {str(e)}'}), 500

@auth_bp.route('/account', methods=['DELETE'])
@token_required
def delete_account():
    """删除账户"""
    try:
        user = request.current_user
        data = request.get_json()
        password = data.get('password')
        
        if not password:
            return jsonify({'error': 'Password is required'}), 400
        
        # 验证密码
        if not verify_password(password, user['password_hash']):
            return jsonify({'error': 'Invalid password'}), 400
        
        # 删除用户（标记为不活跃）
        user['is_active'] = False
        _sync_user_to_db(user, is_active=False)
        _sync_user_to_memory(user, is_active=False)
        
        return jsonify({'message': 'Account deleted successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to delete account: {str(e)}'}), 500

# 管理员专属功能
@auth_bp.route('/admin/users', methods=['GET'])
@token_required
def admin_get_users():
    """管理员获取所有用户列表"""
    user = request.current_user
    if user['role'] != 'admin':
        return jsonify({'error': 'Access denied. Admin role required.'}), 403
    
    try:
        # 如果有数据库可用，则从数据库查询
        if HAVE_DB:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 100))
            q = User.query.order_by(User.created_at.desc())
            pag = q.paginate(page=page, per_page=per_page, error_out=False)
            users = [u.to_dict() for u in pag.items]
            return jsonify({'users': users, 'total': pag.total, 'page': page}), 200

        users_list = []
        for username, user_data in USERS_DB.items():
            users_list.append({
                'id': user_data['id'],
                'username': user_data['username'],
                'email': user_data['email'],
                'role': user_data['role'],
                'is_active': user_data['is_active'],
                'is_verified': user_data['is_verified'],
                'created_at': user_data['created_at']
            })
        return jsonify({'users': users_list}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to get users: {str(e)}'}), 500


@auth_bp.route('/admin/users', methods=['POST'])
@token_required
def admin_create_user():
    """管理员创建用户"""
    user = request.current_user
    if user['role'] != 'admin':
        return jsonify({'error': 'Access denied. Admin role required.'}), 403
    try:
        data = request.get_json() or {}
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'user')
        if not username or not email or not password:
            return jsonify({'error': 'username,email,password required'}), 400
        if get_user_by_username(username) or get_user_by_email(email):
            return jsonify({'error': 'username or email already exists'}), 409

        password_hash = hash_password(password)

        # 写入数据库或内存
        if HAVE_DB:
            u = User(username=username, email=email, password_hash=password_hash, role=role, is_active=True)
            db.session.add(u)
            db.session.commit()
            return jsonify({'user': u.to_dict()}), 201

        # memory fallback
        new_id = max([u['id'] for u in USERS_DB.values()]) + 1
        USERS_DB[username] = {
            'id': new_id,
            'username': username,
            'email': email,
            'password_hash': password_hash,
            'role': role,
            'avatar': '',
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'is_active': True,
            'is_verified': False
        }
        return jsonify({'user': USERS_DB[username]}), 201
    except Exception as e:
        return jsonify({'error': f'Failed to create user: {str(e)}'}), 500


@auth_bp.route('/admin/users/<int:user_id>', methods=['GET','PUT','DELETE'])
@token_required
def admin_user_detail(user_id):
    user = request.current_user
    if user['role'] != 'admin':
        return jsonify({'error': 'Access denied. Admin role required.'}), 403

    try:
        if HAVE_DB:
            u = User.query.get(user_id)
            if not u:
                return jsonify({'error': 'User not found'}), 404
            if request.method == 'GET':
                return jsonify({'user': u.to_dict()}), 200
            if request.method == 'PUT':
                data = request.get_json() or {}
                for f in ['email','role','avatar','is_active','is_verified']:
                    if f in data:
                        setattr(u, f, data[f])
                db.session.commit()
                return jsonify({'user': u.to_dict()}), 200
            if request.method == 'DELETE':
                db.session.delete(u)
                db.session.commit()
                return jsonify({'message': 'User deleted'}), 200

        # memory fallback
        target = None
        for k, v in USERS_DB.items():
            if v['id'] == user_id:
                target = k
                break
        if not target:
            return jsonify({'error': 'User not found'}), 404
        if request.method == 'GET':
            return jsonify({'user': USERS_DB[target]}), 200
        if request.method == 'PUT':
            data = request.get_json() or {}
            for f in ['email','role','avatar','is_active','is_verified']:
                if f in data:
                    USERS_DB[target][f] = data[f]
            return jsonify({'user': USERS_DB[target]}), 200
        if request.method == 'DELETE':
            del USERS_DB[target]
            return jsonify({'message': 'User deleted'}), 200
    except Exception as e:
        return jsonify({'error': f'Admin user detail failed: {str(e)}'}), 500

@auth_bp.route('/admin/users/<int:user_id>/toggle-status', methods=['PUT'])
@token_required
def admin_toggle_user_status(user_id):
    """管理员切换用户激活状态"""
    user = request.current_user
    if user['role'] != 'admin':
        return jsonify({'error': 'Access denied. Admin role required.'}), 403
    
    try:
        # 优先更新数据库记录
        if HAVE_DB:
            u = User.query.get(user_id)
            if not u:
                return jsonify({'error': 'User not found'}), 404
            if u.id == user['id']:
                return jsonify({'error': 'Cannot deactivate your own account'}), 400
            u.is_active = not u.is_active
            db.session.commit()
            return jsonify({
                'message': f"User {'activated' if u.is_active else 'deactivated'} successfully",
                'user': {
                    'id': u.id,
                    'username': u.username,
                    'is_active': u.is_active
                }
            }), 200

        target_user = get_user_by_id(user_id)
        if not target_user:
            return jsonify({'error': 'User not found'}), 404
        # 不允许管理员禁用自己的账户
        if target_user['id'] == user['id']:
            return jsonify({'error': 'Cannot deactivate your own account'}), 400
        target_user['is_active'] = not target_user['is_active']
        return jsonify({
            'message': f"User {'activated' if target_user['is_active'] else 'deactivated'} successfully",
            'user': {
                'id': target_user['id'],
                'username': target_user['username'],
                'is_active': target_user['is_active']
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to toggle user status: {str(e)}'}), 500

@auth_bp.route('/admin/system-stats', methods=['GET'])
@token_required
def admin_get_system_stats():
    """管理员获取系统统计信息"""
    user = request.current_user
    if user['role'] != 'admin':
        return jsonify({'error': 'Access denied. Admin role required.'}), 403
    
    try:
        if HAVE_DB:
            total_users = User.query.count()
            active_users = User.query.filter_by(is_active=True).count()
            verified_users = User.query.filter_by(is_verified=True).count()
            admin_count = User.query.filter_by(role='admin').count()
            stats = {
                'total_users': total_users,
                'active_users': active_users,
                'verified_users': verified_users,
                'admin_count': admin_count,
                'inactive_users': total_users - active_users,
                'unverified_users': total_users - verified_users,
                'regular_users': total_users - admin_count
            }
            return jsonify({'stats': stats}), 200

        total_users = len(USERS_DB)
        active_users = len([u for u in USERS_DB.values() if u['is_active']])
        verified_users = len([u for u in USERS_DB.values() if u['is_verified']])
        admin_count = len([u for u in USERS_DB.values() if u['role'] == 'admin'])
        stats = {
            'total_users': total_users,
            'active_users': active_users,
            'verified_users': verified_users,
            'admin_count': admin_count,
            'inactive_users': total_users - active_users,
            'unverified_users': total_users - verified_users,
            'regular_users': total_users - admin_count
        }
        return jsonify({'stats': stats}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to get system stats: {str(e)}'}), 500

def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = request.current_user
        if user['role'] != 'admin':
            return jsonify({'error': 'Access denied. Admin role required.'}), 403
        return f(*args, **kwargs)
    return decorated

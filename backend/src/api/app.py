"""
人脸情绪识别系统 - Flask后端API
支持三种模型: CNN, VGG16, SE-Net
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import tensorflow as tf
from tensorflow import keras
import numpy as np
import cv2
import base64
from PIL import Image
import io
import os
from datetime import datetime
from typing import Tuple, Optional
from src.ml.image_preprocess import (
    preprocess_for_model,
    infer_input_shape_from_keras,
    infer_input_shape_from_saved_model,
    detect_and_align_mtcnn,
    enhance_clarity,
)
from src.ml.face_quality import assess_face_quality, get_quality_level
from src.auth import auth_bp, token_required, token_required_or_query, admin_required, verify_token, get_user_by_id, hash_password
from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified
from src.ml.video_processor import (
    VideoEmotionProcessor,
    create_emotion_timeline,
    calculate_emotion_statistics
)
from src.config.settings import (
    MODEL_PATHS,
    MODEL_CONFIG,
    UPLOAD_FOLDER,
    DATABASE_URI,
)
import logging
import time
from functools import wraps
from pathlib import Path
from werkzeug.utils import secure_filename

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path(UPLOAD_FOLDER).resolve()


def _safe_user_dirname(username: Optional[str]) -> str:
    """Sanitize username for use as a single path segment (no traversal)."""
    cleaned = secure_filename((username or 'anonymous').strip()) or 'anonymous'
    return cleaned


def _to_upload_relpath(abs_or_rel) -> str:
    """Store DB paths relative to UPLOAD_FOLDER using forward slashes."""
    path = Path(abs_or_rel)
    try:
        if not path.is_absolute():
            path = (UPLOAD_ROOT / path).resolve()
        else:
            path = path.resolve()
        return path.relative_to(UPLOAD_ROOT).as_posix()
    except Exception:
        return Path(abs_or_rel).name


def _resolve_upload_file(filename: str) -> Optional[Path]:
    """Resolve a client-provided upload path; reject traversal / missing files."""
    if not filename or filename.startswith(('/', '\\')):
        return None
    parts = Path(filename.replace('\\', '/')).parts
    if any(p in ('', '.', '..') for p in parts):
        return None
    target = (UPLOAD_ROOT.joinpath(*parts)).resolve()
    try:
        target.relative_to(UPLOAD_ROOT)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def _user_can_access_upload(rel_path: str, current_user: dict) -> bool:
    """仅允许资源所有者或管理员读取 uploads 下文件。"""
    if current_user.get('role') == 'admin':
        return True
    safe_user = _safe_user_dirname(current_user.get('username'))
    parts = Path(rel_path.replace('\\', '/')).parts
    if len(parts) >= 2 and parts[0] in ('predictions', 'video_frames'):
        return parts[1] == safe_user
    return False


app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 上传限制（默认）
# 整体请求大小上限（防止恶意请求导致内存耗尽）
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
# 图像单文件最大限制（前端建议16MB）
app.config['MAX_IMAGE_BYTES'] = 16 * 1024 * 1024
# 视频单文件最大限制（可根据需要调整）
app.config['MAX_VIDEO_BYTES'] = 200 * 1024 * 1024

# 数据库配置 (SQLite 默认，可按需改为其他 DB)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 导入并初始化数据库模块
from src.storage.database import (
    db, init_db, PredictionHistory, User, 
    UserEmotionSummary, HealthAssessment,
    VideoAnalysisResult, EmotionJournal, GratitudeRecord
)
init_db(app)

# 注册认证蓝图
app.register_blueprint(auth_bp)

# 注册心理健康API蓝图
from src.api.health import health_bp
app.register_blueprint(health_bp)

# 情绪类别标签
EMOTION_LABELS = ['anger', 'disgust', 'fear', 'happy', 'normal', 'sad', 'surprised']
EMOTION_LABELS_CN = ['生气', '厌恶', '害怕', '高兴', '平静', '悲伤', '惊讶']

# 全局变量存储已加载的模型
models = {}

# 性能监控装饰器
def timing_decorator(operation_name):
    """性能监控装饰器"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = f(*args, **kwargs)
            duration = time.time() - start
            logger.info(f"⏱️  {operation_name} 耗时: {duration:.3f}秒")
            return result
        return wrapper
    return decorator

def _wrap_saved_model(loaded):
    """将 tf.saved_model.load 返回对象包装为统一结构。"""
    infer = None
    if hasattr(loaded, 'signatures') and 'serving_default' in loaded.signatures:
        infer = loaded.signatures['serving_default']
    input_shape = infer_input_shape_from_saved_model(loaded)
    return {
        'type': 'saved',
        'obj': loaded,
        'infer': infer,
        'input_shape': input_shape,
    }

def _wrap_keras_model(model):
    """将 Keras 模型包装为统一结构。"""
    input_shape = infer_input_shape_from_keras(model)
    return {
        'type': 'keras',
        'obj': model,
        'infer': None,
        'input_shape': input_shape,
    }

def load_model(model_name):
    """加载指定的模型，返回统一包装：{'type': 'keras'|'saved', 'obj': ..., 'infer': ..., 'input_shape': (H,W,C)}"""
    model_path = MODEL_PATHS.get(model_name)
    if not model_path or not os.path.exists(model_path):
        logger.error(f"模型文件不存在: {model_path}")
        return None

    # 1) 优先使用 Keras 加载（适用于 .h5 或包含 keras_metadata 的 SavedModel）
    try:
        model = keras.models.load_model(model_path)
        wrapper = _wrap_keras_model(model)
        logger.info(f"成功加载模型: {model_name} (Keras, from {model_path})")
        return wrapper
    except Exception as e:
        logger.warning(f"Keras 加载失败 {model_name}: {e}; 将尝试 tf.saved_model.load() 回退方式")

    # 2) 回退到 SavedModel 加载（适用于用 tf.saved_model.save 导出的目录）
    try:
        loaded = tf.saved_model.load(model_path)
        wrapper = _wrap_saved_model(loaded)
        logger.info(f"成功加载模型: {model_name} (SavedModel, from {model_path})")
        return wrapper
    except Exception as e2:
        logger.error(f"加载模型失败 {model_name}: {str(e2)}")
        return None

def run_inference(model_entry, x: np.ndarray) -> np.ndarray:
    """对包装后的模型进行推理，返回 numpy 数组预测结果。"""
    if model_entry is None:
        raise ValueError('模型未加载')
    if model_entry['type'] == 'keras':
        return model_entry['obj'].predict(x, verbose=0)
    # SavedModel
    infer = model_entry.get('infer')
    if infer is None:
        raise ValueError('SavedModel 缺少 serving_default 签名，无法推理')
    input_key = list(infer.structured_input_signature[1].keys())[0]
    outputs = infer(**{input_key: tf.constant(x)})
    out_key = list(outputs.keys())[0]
    return outputs[out_key].numpy()


def _batch_array_to_data_url(arr: np.ndarray) -> str:
    """将形状为 (1,H,W,C) 且范围[0,1]的数组转为 data:image/jpeg;base64, 字符串。"""
    try:
        x = np.squeeze(arr, axis=0)
        if x.ndim == 2:
            img = Image.fromarray((x * 255.0).clip(0, 255).astype(np.uint8), mode='L')
        else:
            # 处理通道
            if x.shape[-1] == 1:
                img = Image.fromarray((x[..., 0] * 255.0).clip(0, 255).astype(np.uint8), mode='L')
            else:
                # 保留前三通道
                if x.shape[-1] > 3:
                    x = x[..., :3]
                # 若是 2 通道，填充为 3 通道
                if x.shape[-1] == 2:
                    pad = np.zeros((*x.shape[:2], 1), dtype=x.dtype)
                    x = np.concatenate([x, pad], axis=-1)
                img = Image.fromarray((x * 255.0).clip(0, 255).astype(np.uint8), mode='RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"预处理图像可视化失败: {e}")
        return ''


def _pil_to_data_url(img: Image.Image) -> str:
    """将 PIL.Image 转为 data:image/jpeg;base64, 字符串。"""
    try:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"PIL 转 dataURL 失败: {e}")
        return ''

def preprocess_image(image_data, target_size=(100, 100)):
    """
    预处理图像
    Args:
        image_data: base64编码的图像或PIL Image对象
        target_size: 目标尺寸
    Returns:
        preprocessed_image: 预处理后的图像数组
    """
    try:
        # 如果是base64字符串,先解码
        if isinstance(image_data, str):
            # 移除data:image/jpeg;base64,前缀
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
        else:
            image = image_data
        
        # 旧函数保留，但此处仅做最基础RGB resize，具体到模型前会再次按模型输入形状处理
        image = image.convert('RGB').resize(target_size)
        arr = np.array(image).astype(np.float32) / 255.0
        arr = np.expand_dims(arr, axis=0)
        return arr
    except Exception as e:
        logger.error(f"图像预处理失败: {str(e)}")
        return None

def detect_face(image):
    """
    使用OpenCV检测人脸
    Args:
        image: PIL Image对象
    Returns:
        face_image: 检测到的人脸图像,如果没有检测到返回原图
    """
    try:
        # 转换为OpenCV格式
        img_array = np.array(image)
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        # 加载人脸检测器
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        
        # 检测人脸
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        
        if len(faces) > 0:
            # 取第一张人脸
            x, y, w, h = faces[0]
            face_img = img_array[y:y+h, x:x+w]
            return Image.fromarray(face_img)
        else:
            logger.warning("未检测到人脸,使用原图")
            return image
    except Exception as e:
        logger.error(f"人脸检测失败: {str(e)}")
        return image

def detect_face_for_display(image, margin_ratio=0.2):
    """
    纯粹用于前端显示的人脸检测和裁剪（不做旋转对齐，避免黑边）
    优先使用 MTCNN 检测，失败则回退到 Haar 级联
    Args:
        image: PIL Image对象
        margin_ratio: 裁剪时的边距比例（相对于人脸框的宽/高）
    Returns:
        face_image: 干净裁剪的人脸图像（不含旋转黑边）
    """
    try:
        from mtcnn import MTCNN
        detector = MTCNN()
        rgb = image.convert('RGB')
        res = detector.detect_faces(np.array(rgb))
        
        if res:
            # 选择置信度最高的人脸
            face = max(res, key=lambda d: d.get('confidence', 0))
            x, y, w, h = face['box']
            
            # 添加边距，确保不超出图像边界
            margin_w = int(w * margin_ratio)
            margin_h = int(h * margin_ratio)
            x1 = max(0, x - margin_w)
            y1 = max(0, y - margin_h)
            x2 = min(rgb.width, x + w + margin_w)
            y2 = min(rgb.height, y + h + margin_h)
            
            # 裁剪人脸区域
            face_crop = rgb.crop((x1, y1, x2, y2))
            logger.info(f"✂️  MTCNN 人脸裁剪成功 (置信度: {face['confidence']:.2%})")
            return face_crop
    except Exception as e:
        logger.warning(f"MTCNN 检测失败，回退到 Haar 检测: {str(e)}")
    
    # 回退方案：使用 Haar 级联检测
    try:
        img_array = np.array(image.convert('RGB'))
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        
        if len(faces) > 0:
            x, y, w, h = faces[0]
            margin_w = int(w * margin_ratio)
            margin_h = int(h * margin_ratio)
            x1 = max(0, x - margin_w)
            y1 = max(0, y - margin_h)
            x2 = min(image.width, x + w + margin_w)
            y2 = min(image.height, y + h + margin_h)
            
            face_crop = image.crop((x1, y1, x2, y2))
            logger.info("✂️  Haar 人脸裁剪成功")
            return face_crop
        else:
            logger.warning("未检测到人脸，返回原图")
            return image
    except Exception as e:
        logger.error(f"Haar 人脸检测失败: {str(e)}")
        return image

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'ok',
        'message': '服务运行正常',
        'available_models': list(MODEL_PATHS.keys())
    })

@app.route('/api/models', methods=['GET'])
def get_models():
    """获取可用的模型列表"""
    model_info = []
    for name, path in MODEL_PATHS.items():
        cfg = MODEL_CONFIG.get(name, {})
        model_info.append({
            'name': name,
            'display_name': name.upper(),
            'available': os.path.exists(path),
            'description': cfg.get('description', ''),
            'accuracy': cfg.get('accuracy'),
        })
    return jsonify({'models': model_info})

def update_health_tables(username, emotion, emotion_cn, confidence, probabilities_cn):
    """
    更新健康相关的数据表
    - UserEmotionSummary: 每日情绪统计汇总
    - HealthAssessment: 心理健康评估
    """
    try:
        from datetime import date
        today = date.today()
        
        # 1. 更新或创建今天的情绪统计汇总
        summary = UserEmotionSummary.query.filter_by(
            username=username,
            summary_date=today
        ).first()
        
        if summary:
            # 更新现有记录
            summary.total_predictions += 1
            
            # 更新情绪计数
            emotion_counts = summary.emotion_counts or {}
            emotion_counts[emotion_cn] = emotion_counts.get(emotion_cn, 0) + 1
            summary.emotion_counts = emotion_counts
            # 标记 JSON 字段已修改（SQLAlchemy 需要）
            flag_modified(summary, 'emotion_counts')
            
            # 更新主导情绪（中文）
            dominant_emotion_cn = max(emotion_counts, key=emotion_counts.get)
            summary.dominant_emotion_cn = dominant_emotion_cn
            summary.dominant_emotion_count = emotion_counts[dominant_emotion_cn]
            
            # 同时更新英文主导情绪
            cn_to_en = {
                '生气': 'anger', '厌恶': 'disgust', '害怕': 'fear',
                '高兴': 'happy', '平静': 'normal', '悲伤': 'sad', '惊讶': 'surprise'
            }
            summary.dominant_emotion = cn_to_en.get(dominant_emotion_cn, dominant_emotion_cn)
            
            # 重新计算正负面情绪比例
            positive_emotions = ['happy', 'surprise']
            negative_emotions = ['sad', 'angry', 'disgust', 'fear']
            
            # 使用正确的中文标签：['生气', '厌恶', '害怕', '高兴', '平静', '悲伤', '惊讶']
            positive_count = sum(emotion_counts.get(e, 0) for e in ['高兴', '惊讶'])
            negative_count = sum(emotion_counts.get(e, 0) for e in ['悲伤', '生气', '厌恶', '害怕'])
            neutral_count = emotion_counts.get('平静', 0)
            
            # 更新计数字段
            summary.positive_count = positive_count
            summary.negative_count = negative_count
            summary.neutral_count = neutral_count
            
            total = summary.total_predictions
            summary.positive_rate = round(positive_count / total, 2) if total > 0 else 0
            summary.negative_rate = round(negative_count / total, 2) if total > 0 else 0
            
            # 计算平均置信度
            if summary.avg_confidence:
                summary.avg_confidence = round((summary.avg_confidence * (total - 1) + confidence) / total, 2)
            else:
                summary.avg_confidence = round(confidence, 2)
            
            summary.updated_at = datetime.now()
        else:
            # 创建新记录
            positive_emotions_cn = ['高兴', '惊讶']
            negative_emotions_cn = ['悲伤', '生气', '厌恶', '害怕']
            
            is_positive = emotion_cn in positive_emotions_cn
            is_negative = emotion_cn in negative_emotions_cn
            
            # 中文到英文的映射
            cn_to_en = {
                '生气': 'anger', '厌恶': 'disgust', '害怕': 'fear',
                '高兴': 'happy', '平静': 'normal', '悲伤': 'sad', '惊讶': 'surprise'
            }
            
            summary = UserEmotionSummary(
                username=username,
                summary_date=today,
                total_predictions=1,
                dominant_emotion=cn_to_en.get(emotion_cn, emotion),  # 英文
                dominant_emotion_cn=emotion_cn,  # 中文
                dominant_emotion_count=1,
                emotion_counts={emotion_cn: 1},
                positive_count=1 if is_positive else 0,
                negative_count=1 if is_negative else 0,
                neutral_count=1 if not is_positive and not is_negative else 0,
                positive_rate=1.0 if is_positive else 0.0,
                negative_rate=1.0 if is_negative else 0.0,
                avg_confidence=round(confidence, 2),
                updated_at=datetime.now()
            )
            db.session.add(summary)
        
        # 2. 创建心理健康评估记录（基于情绪统计）
        # 使用与数据分析界面相同的算法
        if summary:
            # 计算积极和消极情绪占比（百分比）
            positive_rate_percent = summary.positive_rate * 100  # 转换为百分比
            negative_rate_percent = summary.negative_rate * 100
            
            # 计算情绪稳定性（简化版，因为没有历史波动数据）
            # 如果有足够数据，稳定性基于情绪分布的均衡度
            if summary.total_predictions >= 3:
                # 标准差的简化计算：基于情绪分布的离散程度
                emotion_counts = summary.emotion_counts or {}
                if emotion_counts:
                    counts = list(emotion_counts.values())
                    mean = sum(counts) / len(counts)
                    variance = sum((x - mean) ** 2 for x in counts) / len(counts)
                    std_dev = variance ** 0.5
                    # 标准差越小，稳定性越高
                    stability = max(0, min(100, 100 - std_dev * 20))
                else:
                    stability = 50
            else:
                # 数据不足，默认中等稳定性
                stability = 50
            
            # 使用与前端相同的公式计算健康得分
            # 总分 = 积极占比 × 0.4 + (100 - 消极占比) × 0.3 + 稳定性 × 0.3
            health_score = int(
                positive_rate_percent * 0.4 + 
                (100 - negative_rate_percent) * 0.3 + 
                stability * 0.3
            )
            
            # 确保在0-100范围内
            health_score = max(0, min(100, health_score))
            
            # 确定评级和建议（与前端一致）
            if health_score >= 85:
                risk_level = 'excellent'
                risk_level_cn = '优秀'
                advice = '您的情绪状态非常健康！保持积极乐观的心态，继续加油！'
            elif health_score >= 70:
                risk_level = 'good'
                risk_level_cn = '良好'
                advice = '您的情绪状态良好，继续保持规律作息和适度运动。'
            elif health_score >= 55:
                risk_level = 'normal'
                risk_level_cn = '一般'
                advice = '建议多参与社交活动，尝试放松技巧，如冥想、瑜伽等。'
            else:
                risk_level = 'need-attention'
                risk_level_cn = '需要关注'
                advice = '您的情绪波动较大，建议咨询专业心理咨询师，及时调整心态。'
            
            # 生成详细建议列表
            suggestions = [advice]
            
            # 保存评估记录
            assessment = HealthAssessment(
                username=username,
                assessment_date=today,
                health_score=health_score,
                risk_level=risk_level,
                risk_level_cn=risk_level_cn,
                emotion_stability=stability / 100.0,  # 转换为0-1的小数
                positive_rate=summary.positive_rate,
                negative_rate=summary.negative_rate,
                suggestions=suggestions,
                based_on_days=1,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            db.session.add(assessment)
        
        db.session.commit()
        logger.info(f"✅ 已更新健康数据表: {username}, {emotion_cn}")
        
    except Exception as e:
        logger.error(f"更新健康数据表失败: {e}")
        db.session.rollback()

@app.route('/api/predict', methods=['POST'])
@token_required
def predict_emotion():
    """
    情绪识别接口
    请求格式:
    {
        "image": "base64编码的图像",
        "model": "cnn|vgg|se",
        "detect_face": true|false
    }
    """
    try:
        data = request.json
        
        if not data or 'image' not in data:
            return jsonify({'error': '缺少图像数据'}), 400
        
        # 获取模型类型
        model_name = data.get('model', 'cnn').lower()
        if model_name not in MODEL_PATHS:
            return jsonify({'error': f'不支持的模型: {model_name}'}), 400
        
        # 加载模型(如果还未加载)
        if model_name not in models:
            model_entry = load_model(model_name)
            if model_entry is None:
                return jsonify({'error': f'模型加载失败: {model_name}'}), 500
            models[model_name] = model_entry
        model_entry = models[model_name]
        
        # 解码图像
        image_data = data['image']
        if ',' in image_data:
            payload = image_data.split(',')[1]
        else:
            payload = image_data

        # 校验 base64 图像大小与格式
        ok, err = validate_base64_image(payload)
        if not ok:
            logger.warning(f"图像校验未通过: {err}")
            return jsonify({'error': f'图像校验失败: {err}'}), 400

        # 解码并打开图像
        image_bytes = base64.b64decode(payload)
        image = Image.open(io.BytesIO(image_bytes))
        original_image = image.copy()
        
        # 是否进行人脸检测与对齐：优先 MTCNN，对齐失败则回退 Haar
        if data.get('detect_face', True):
            # 用于前端显示：只检测和裁剪，不旋转对齐（避免黑边）
            display_face = detect_face_for_display(image)
            
            # 用于模型预测：完整的检测和对齐流程（可能有黑边，但模型需要）
            aligned = detect_and_align_mtcnn(image)
            if aligned is not None:
                image = aligned
            else:
                image = detect_face(image)
        else:
            display_face = image.copy()
        
        # 保存用于前端显示的干净人脸图（无黑边）
        aligned_face = display_face.copy()
        
        # 评估人脸质量
        quality_start = time.time()
        quality_result = assess_face_quality(aligned_face)
        quality_time = time.time() - quality_start
        logger.info(f"🔍 人脸质量评估: {quality_result['quality_score']:.1f}分 (耗时: {quality_time:.3f}秒)")
        
        # 如果质量过低,给出警告
        if not quality_result['is_acceptable']:
            logger.warning(f"⚠️  人脸质量较低: {', '.join(quality_result['warnings'])}")
        
        # 根据模型类型选择预处理模式
        if model_name == 'vgg':
            preprocess_mode = 'vgg'
        elif model_name in ('se81', 'se83'):
            preprocess_mode = 'efficientnet'
        else:
            preprocess_mode = 'simple'
        
        # 预处理图像（根据模型输入形状动态处理）
        # 修复：CNN使用96×96×1，SE和VGG模型使用224×224×3
        preprocess_start = time.time()
        fallback = (96, 96, 1) if model_name in ("cnn",) else (224, 224, 3)
        processed_image = preprocess_for_model(
            image,
            model=model_entry['obj'] if model_entry['type'] == 'keras' else None,
            loaded=model_entry['obj'] if model_entry['type'] == 'saved' else None,
            fallback=model_entry.get('input_shape') or fallback,
            mode=preprocess_mode
        )
        if processed_image is None:
            return jsonify({'error': '图像预处理失败'}), 500
        preprocess_time = time.time() - preprocess_start
        logger.info(f"🔧 预处理完成 (模式: {preprocess_mode}, 耗时: {preprocess_time:.3f}秒)")

        # 进行预测（兼容 Keras 与 SavedModel）
        inference_start = time.time()
        predictions = run_inference(model_entry, processed_image)
        predicted_class = np.argmax(predictions[0])
        confidence = float(predictions[0][predicted_class])
        inference_time = time.time() - inference_start
        logger.info(f"🤖 推理完成: {EMOTION_LABELS[predicted_class]} ({confidence:.2%}, 耗时: {inference_time:.3f}秒)")
        # 展示对齐后的人脸图（即模型实际预测的输入图）
        preproc_data_url = _pil_to_data_url(aligned_face)
        
        # 构建返回结果
        quality_level, quality_color = get_quality_level(quality_result['quality_score'])
        result = {
            'success': True,
            'emotion': EMOTION_LABELS[predicted_class],
            'emotion_cn': EMOTION_LABELS_CN[predicted_class],
            'confidence': confidence,
            'probabilities': {
                EMOTION_LABELS[i]: float(predictions[0][i])
                for i in range(len(EMOTION_LABELS))
            },
            'probabilities_cn': {
                EMOTION_LABELS_CN[i]: float(predictions[0][i])
                for i in range(len(EMOTION_LABELS))
            },
            'model_used': model_name.upper(),
            'timestamp': datetime.now().isoformat(),
            'preprocessed_image': preproc_data_url,
            # 新增: 人脸质量信息
            'face_quality': {
                'score': quality_result['quality_score'],
                'level': quality_level,
                'color': quality_color,
                'blur_score': quality_result['blur_score'],
                'brightness': quality_result['brightness'],
                'contrast': quality_result['contrast'],
                'warnings': quality_result['warnings'],
                'is_acceptable': quality_result['is_acceptable']
            },
            # 新增: 性能信息
            'performance': {
                'quality_assessment_time': round(quality_time, 3),
                'preprocessing_time': round(preprocess_time, 3),
                'inference_time': round(inference_time, 3),
                'total_time': round(quality_time + preprocess_time + inference_time, 3)
            }
        }
        
        logger.info(f"✅ 预测成功: {result['emotion_cn']} (置信度: {confidence:.2%}, 总耗时: {result['performance']['total_time']:.3f}秒)")

        # 尝试将预测记录保存到数据库（轻量级：只存文件路径）
        try:
            username_for_history = request.current_user.get('username')
            
            # 保存图片文件到服务器（可选：如果需要持久化）
            original_image_path = None
            preprocessed_image_path = None
            
            if username_for_history:  # 只为登录用户保存文件
                try:
                    # 确保 uploads/predictions 目录存在
                    safe_user = _safe_user_dirname(username_for_history)
                    predictions_dir = os.path.join(str(UPLOAD_FOLDER), 'predictions', safe_user)
                    os.makedirs(predictions_dir, exist_ok=True)
                    
                    # 生成唯一文件名
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    
                    # 保存预处理后的人脸图片（缩略图）
                    preprocessed_filename = f"face_{timestamp}.jpg"
                    preprocessed_path = os.path.join(predictions_dir, preprocessed_filename)
                    aligned_face.save(preprocessed_path, quality=85)
                    preprocessed_image_path = _to_upload_relpath(preprocessed_path)
                    
                    logger.info(f"💾 已保存图片到: {preprocessed_path}")
                except Exception as save_error:
                    logger.warning(f"保存图片文件失败: {save_error}")
            
            # 保存轻量级元数据到数据库
            history = PredictionHistory(
                emotion=result['emotion'],
                emotion_cn=result['emotion_cn'],
                confidence=confidence,
                model_used=model_name.upper(),
                username=username_for_history,
                original_image_path=original_image_path,
                preprocessed_image_path=preprocessed_image_path,
                probabilities={
                    'en': result['probabilities'],
                    'cn': result['probabilities_cn']
                },
                input_type='image'
            )
            db.session.add(history)
            db.session.commit()
            result['history_id'] = history.id
            
            # 更新健康数据表（情绪统计、健康评估）
            if username_for_history:
                update_health_tables(
                    username=username_for_history,
                    emotion=result['emotion'],
                    emotion_cn=result['emotion_cn'],
                    confidence=confidence,
                    probabilities_cn=result['probabilities_cn']
                )
        except Exception as e:
            logger.warning(f"保存预测历史到数据库失败: {e}")

        return jsonify(result)
        
    except Exception as e:
        logger.error(f"预测失败: {str(e)}")
        return jsonify({'error': f'预测失败: {str(e)}'}), 500

@app.route('/api/batch_predict', methods=['POST'])
@token_required
def batch_predict():
    """批量预测接口"""
    try:
        data = request.json
        
        if not data or 'images' not in data:
            return jsonify({'error': '缺少图像数据'}), 400
        
        images = data['images']
        model_name = data.get('model', 'cnn').lower()
        
        # 加载模型
        if model_name not in models:
            model_entry = load_model(model_name)
            if model_entry is None:
                return jsonify({'error': f'模型加载失败: {model_name}'}), 500
            models[model_name] = model_entry
        
        results = []
        for idx, img_data in enumerate(images):
            try:
                # 预处理
                if ',' in img_data:
                    img_data = img_data.split(',')[1]
                image_bytes = base64.b64decode(img_data)
                image = Image.open(io.BytesIO(image_bytes))
                original_image = image.copy()
                
                if data.get('detect_face', True):
                    # 用于前端显示：只检测和裁剪，不旋转对齐（避免黑边）
                    display_face = detect_face_for_display(image)
                    
                    # 用于模型预测：完整的检测和对齐流程（可能有黑边，但模型需要）
                    aligned = detect_and_align_mtcnn(image)
                    if aligned is not None:
                        image = aligned
                    else:
                        image = detect_face(image)
                else:
                    display_face = image.copy()
                
                # 保存用于前端显示的干净人脸图（无黑边）
                aligned_face = display_face.copy()
                
                # 根据模型类型选择预处理模式
                if model_name == 'vgg':
                    preprocess_mode = 'vgg'
                elif model_name in ('se81', 'se83'):
                    preprocess_mode = 'efficientnet'
                else:
                    preprocess_mode = 'simple'
                
                model_entry = models[model_name]
                # 修复：CNN使用96×96×1，SE和VGG模型使用224×224×3
                fallback = (96, 96, 1) if model_name in ("cnn",) else (224, 224, 3)
                processed_image = preprocess_for_model(
                    image,
                    model=model_entry['obj'] if model_entry['type'] == 'keras' else None,
                    loaded=model_entry['obj'] if model_entry['type'] == 'saved' else None,
                    fallback=model_entry.get('input_shape') or fallback,
                    mode=preprocess_mode
                )
                
                # 预测
                predictions = run_inference(models[model_name], processed_image)
                predicted_class = np.argmax(predictions[0])
                # 展示对齐后的人脸图（即模型实际预测的输入图）
                preproc_data_url = _pil_to_data_url(aligned_face)
                
                results.append({
                    'index': idx,
                    'emotion': EMOTION_LABELS[predicted_class],
                    'emotion_cn': EMOTION_LABELS_CN[predicted_class],
                    'confidence': float(predictions[0][predicted_class]),
                    'preprocessed_image': preproc_data_url
                })
                # 保存每条记录到数据库
                try:
                    username_for_history = None
                    auth_header = request.headers.get('Authorization')
                    if auth_header and ' ' in auth_header:
                        token = auth_header.split(' ')[1]
                        payload = verify_token(token)
                        if payload and payload.get('user_id'):
                            u = get_user_by_id(payload['user_id'])
                            if u:
                                username_for_history = u.get('username')
                    history = PredictionHistory(
                        emotion=EMOTION_LABELS[predicted_class],
                        emotion_cn=EMOTION_LABELS_CN[predicted_class],
                        confidence=float(predictions[0][predicted_class]),
                        model_used=model_name.upper(),
                        image_path=None,
                        username=username_for_history
                    )
                    db.session.add(history)
                    db.session.commit()
                    results[-1]['history_id'] = history.id
                except Exception as e:
                    logger.warning(f"批量保存预测历史失败: {e}")
            except Exception as e:
                results.append({
                    'index': idx,
                    'error': str(e)
                })
        
        return jsonify({
            'success': True,
            'results': results,
            'model_used': model_name.upper()
        })
        
    except Exception as e:
        logger.error(f"批量预测失败: {str(e)}")
        return jsonify({'error': f'批量预测失败: {str(e)}'}), 500

def warmup_models():
    """启动时预热所有模型,加快首次请求响应"""
    logger.info("=" * 60)
    logger.info("🚀 开始模型预热...")
    logger.info("=" * 60)
    
    dummy_img = Image.new('RGB', (112, 112), (128, 128, 128))
    
    for model_name in MODEL_PATHS.keys():
        try:
            logger.info(f"⏳ 预热模型: {model_name.upper()}")
            start_time = time.time()
            
            # 加载模型
            model_entry = load_model(model_name)
            if model_entry is None:
                logger.warning(f"⚠️  模型 {model_name} 加载失败,跳过")
                continue
            
            models[model_name] = model_entry
            
            # 选择预处理模式
            if model_name == 'vgg':
                preprocess_mode = 'vgg'
            elif model_name in ('se81', 'se83'):
                preprocess_mode = 'efficientnet'
            else:
                preprocess_mode = 'simple'
            
            # 预处理
            # 修复：CNN使用96×96×1，SE和VGG模型使用224×224×3
            fallback = (96, 96, 1) if model_name == 'cnn' else (224, 224, 3)
            processed = preprocess_for_model(
                dummy_img,
                model=model_entry['obj'] if model_entry['type'] == 'keras' else None,
                loaded=model_entry['obj'] if model_entry['type'] == 'saved' else None,
                fallback=fallback,
                mode=preprocess_mode
            )
            
            # 推理
            _ = run_inference(model_entry, processed)
            
            duration = time.time() - start_time
            logger.info(f"✅ 模型 {model_name.upper()} 预热完成 (耗时: {duration:.2f}秒)")
            
        except Exception as e:
            logger.error(f"❌ 模型 {model_name} 预热失败: {str(e)}")
    
    logger.info("=" * 60)
    logger.info(f"✅ 模型预热完成! 已加载 {len(models)}/{len(MODEL_PATHS)} 个模型")
    logger.info("=" * 60)


# ==================== 视频情绪识别API ====================

# 初始化视频处理器
video_processor = VideoEmotionProcessor(upload_folder=str(UPLOAD_FOLDER))

# 允许的视频格式
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv'}
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP', 'BMP'}


def _estimate_base64_bytes(b64str: str) -> int:
    """估算 base64 字符串解码后的字节长度（不含 data: 前缀）。"""
    # base64 长度 * 3 / 4 为近似二进制字节数
    return int(len(b64str) * 3 / 4)


def validate_base64_image(payload: str) -> Tuple[bool, str]:
    """验证 base64 payload 是否为支持的图像格式且不超过大小限制。
    返回 (is_valid, error_message)。
    """
    try:
        # 估算大小
        estimated = _estimate_base64_bytes(payload)
        if estimated > app.config.get('MAX_IMAGE_BYTES', 16 * 1024 * 1024):
            return False, f"图像大小超过限制 ({estimated} bytes)"

        # 尝试解码并打开
        image_bytes = base64.b64decode(payload)
        img = Image.open(io.BytesIO(image_bytes))
        fmt = (img.format or '').upper()
        if fmt not in ALLOWED_IMAGE_FORMATS:
            return False, f"不支持的图像格式: {fmt}"
        return True, ''
    except Exception as e:
        return False, f"图像校验失败: {str(e)}"

def allowed_video_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


@app.route('/api/video/upload', methods=['POST'])
@token_required
def upload_video():
    """
    上传视频文件
    请求格式: multipart/form-data
    Returns:
        {
            "success": true,
            "video_id": "video_20231115_143020.mp4",
            "video_info": {...},
            "thumbnail": "base64..."
        }
    """
    try:
        # 检查是否有文件
        if 'video' not in request.files:
            return jsonify({'error': '没有上传视频文件'}), 400
        
        file = request.files['video']
        
        if file.filename == '':
            return jsonify({'error': '文件名为空'}), 400
        
        if not allowed_video_file(file.filename):
            return jsonify({
                'error': f'不支持的视频格式，支持的格式: {", ".join(ALLOWED_VIDEO_EXTENSIONS)}'
            }), 400
        
        # 文件大小预检（基于请求头）
        content_length = request.content_length or 0
        if content_length > app.config.get('MAX_VIDEO_BYTES', 200 * 1024 * 1024):
            logger.warning(f"视频上传过大: {content_length} bytes")
            return jsonify({'error': '上传视频过大'}), 413

        # 保存视频文件
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        video_id = f"video_{timestamp}_{filename}"

        video_data = file.read()
        # 双重保护：再检查一次实际读取字节数
        if len(video_data) > app.config.get('MAX_VIDEO_BYTES', 200 * 1024 * 1024):
            logger.warning(f"视频读取后发现过大: {len(video_data)} bytes")
            return jsonify({'error': '上传视频过大'}), 413

        video_path = video_processor.save_video(video_data, video_id)
        
        # 获取视频信息
        video_info = video_processor.get_video_info(video_path)
        
        # 获取缩略图
        thumbnail = video_processor.get_thumbnail(video_path)
        
        logger.info(f"✅ 视频上传成功: {video_id}")
        
        return jsonify({
            'success': True,
            'video_id': video_id,
            'video_info': video_info,
            'thumbnail': thumbnail,
            'message': '视频上传成功'
        })
    
    except Exception as e:
        logger.error(f"❌ 视频上传失败: {str(e)}")
        return jsonify({'error': f'视频上传失败: {str(e)}'}), 500


@app.route('/api/video/analyze', methods=['POST'])
@token_required
def analyze_video():
    """
    分析视频中的情绪
    请求格式:
    {
        "video_id": "video_20231115_143020.mp4",
        "model": "cnn",
        "interval": 5.0,
        "max_frames": 100,
        "detect_face": true
    }
    Returns:
        {
            "success": true,
            "video_id": "...",
            "total_frames": 10,
            "frames": [...],
            "timeline": {...},
            "statistics": {...}
        }
    """
    try:
        data = request.json
        
        if not data or 'video_id' not in data:
            return jsonify({'error': '缺少video_id参数'}), 400
        
        video_id = data['video_id']
        # 禁止路径穿越：video_id 只能是 upload 目录下的单层安全文件名
        safe_id = Path(str(video_id)).name
        if safe_id != video_id or '..' in video_id or '/' in str(video_id) or '\\' in str(video_id):
            return jsonify({'error': '非法的 video_id'}), 400
        video_path = str((Path(video_processor.upload_folder) / safe_id).resolve())
        upload_root = Path(video_processor.upload_folder).resolve()
        try:
            Path(video_path).relative_to(upload_root)
        except ValueError:
            return jsonify({'error': '非法的 video_id'}), 400
        
        if not os.path.exists(video_path):
            return jsonify({'error': f'视频文件不存在: {video_id}'}), 404
        
        video_id = safe_id
        
        # 获取参数
        model_name = data.get('model', 'cnn').lower()
        interval = float(data.get('interval', 5.0))
        max_frames = int(data.get('max_frames', 100))
        detect_face = data.get('detect_face', True)
        
        if model_name not in MODEL_PATHS:
            return jsonify({'error': f'不支持的模型: {model_name}'}), 400
        
        # 加载模型
        if model_name not in models:
            model_entry = load_model(model_name)
            if model_entry is None:
                return jsonify({'error': f'模型加载失败: {model_name}'}), 500
            models[model_name] = model_entry
        
        model_entry = models[model_name]
        
        logger.info(f"🎬 开始分析视频: {video_id}, 模型={model_name}, 间隔={interval}秒")
        
        # 提取视频帧
        extract_start = time.time()
        frames = video_processor.extract_frames(video_path, interval, max_frames)
        extract_time = time.time() - extract_start
        
        logger.info(f"✅ 提取完成: {len(frames)} 帧 (耗时: {extract_time:.2f}秒)")
        
        # 🔑 提前获取当前用户信息（避免在循环中重复获取）
        current_username = None
        try:
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
                payload = verify_token(token)
                if payload:
                    user_id = payload.get('user_id')
                    user = get_user_by_id(user_id)
                    if user:
                        current_username = user.get('username')
                        logger.info(f"👤 检测到登录用户: {current_username}")
        except Exception as e:
            logger.warning(f"获取用户信息失败: {e}")
        
        # 对每一帧进行情绪识别
        analysis_results = []
        predict_start = time.time()
        
        for idx, (timestamp, frame_rgb, frame_base64) in enumerate(frames):
            try:
                # 转换为PIL Image
                image = Image.fromarray(frame_rgb)
                
                # 人脸检测
                if detect_face:
                    display_face = detect_face_for_display(image)
                    aligned = detect_and_align_mtcnn(image)
                    if aligned is not None:
                        image = aligned
                    else:
                        image = detect_face(image)
                else:
                    display_face = image.copy()
                
                aligned_face = display_face.copy()
                
                # 选择预处理模式
                if model_name == 'vgg':
                    preprocess_mode = 'vgg'
                elif model_name in ('se81', 'se83'):
                    preprocess_mode = 'efficientnet'
                else:
                    preprocess_mode = 'simple'
                
                # 预处理
                fallback = (96, 96, 1) if model_name == 'cnn' else (224, 224, 3)
                processed_image = preprocess_for_model(
                    image,
                    model=model_entry['obj'] if model_entry['type'] == 'keras' else None,
                    loaded=model_entry['obj'] if model_entry['type'] == 'saved' else None,
                    fallback=model_entry.get('input_shape') or fallback,
                    mode=preprocess_mode
                )
                
                if processed_image is None:
                    logger.warning(f"⚠️  帧 {idx+1} 预处理失败，跳过")
                    continue
                
                # 进行预测
                predictions = run_inference(model_entry, processed_image)
                predicted_class = np.argmax(predictions[0])
                confidence = float(predictions[0][predicted_class])
                
                # 生成对齐后的人脸图base64
                aligned_base64 = _pil_to_data_url(aligned_face)
                
                # 格式化时间
                minutes = int(timestamp // 60)
                seconds = int(timestamp % 60)
                time_formatted = f"{minutes:02d}:{seconds:02d}"
                
                result = {
                    'frame_index': idx,
                    'timestamp': timestamp,
                    'time_formatted': time_formatted,
                    'emotion': EMOTION_LABELS[predicted_class],
                    'emotion_cn': EMOTION_LABELS_CN[predicted_class],
                    'confidence': confidence,
                    'original_frame': frame_base64,
                    'face_image': aligned_base64,
                    'probabilities': {
                        EMOTION_LABELS[i]: float(predictions[0][i])
                        for i in range(len(EMOTION_LABELS))
                    },
                    'probabilities_cn': {
                        EMOTION_LABELS_CN[i]: float(predictions[0][i])
                        for i in range(len(EMOTION_LABELS))
                    }
                }
                
                analysis_results.append(result)
                logger.info(f"  ✓ 帧 {idx+1}/{len(frames)}: {time_formatted} - {EMOTION_LABELS_CN[predicted_class]} ({confidence:.2%})")
                
                # 💾 保存视频帧到数据库
                try:
                    # 保存视频帧图片到文件系统
                    frame_filename = None
                    frame_path = None
                    if aligned_face:
                        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S%f')
                        username_folder = _safe_user_dirname(current_username)
                        frame_dir = os.path.join(str(UPLOAD_FOLDER), 'video_frames', username_folder)
                        os.makedirs(frame_dir, exist_ok=True)
                        
                        frame_filename = f"frame_{timestamp_str}_idx{idx}.jpg"
                        frame_path = os.path.join(frame_dir, frame_filename)
                        
                        # 保存人脸图片
                        aligned_face.save(frame_path, 'JPEG', quality=85)
                        logger.info(f"💾 保存视频帧图片: {frame_path}")
                        frame_path = _to_upload_relpath(frame_path)
                    
                    # 保存到数据库（记录用户名）
                    history = PredictionHistory(
                        emotion=EMOTION_LABELS[predicted_class],
                        emotion_cn=EMOTION_LABELS_CN[predicted_class],
                        confidence=confidence,
                        model_used=model_name.upper(),
                        username=current_username,  # 保存用户名（可能是 None）
                        preprocessed_image_path=frame_path,
                        video_path=video_path,
                        frame_timestamp=timestamp,
                        frame_index=idx,
                        probabilities=result['probabilities'],
                        input_type='video'
                    )
                    db.session.add(history)
                    db.session.commit()
                    
                    username_display = current_username or 'anonymous'
                    logger.info(f"📝 保存视频帧历史记录: ID={history.id}, 用户={username_display}, 帧={idx}")
                    
                    # 🔄 为每一帧更新情绪汇总表（如果有登录用户）
                    if current_username:
                        try:
                            update_health_tables(
                                username=current_username,
                                emotion=EMOTION_LABELS[predicted_class],
                                emotion_cn=EMOTION_LABELS_CN[predicted_class],
                                confidence=confidence,
                                probabilities_cn=result['probabilities_cn']
                            )
                            logger.debug(f"✅ 已更新帧{idx}的情绪汇总")
                        except Exception as update_error:
                            logger.warning(f"⚠️  更新帧{idx}的情绪汇总失败: {update_error}")
                
                except Exception as save_error:
                    logger.error(f"⚠️  保存视频帧历史记录失败: {save_error}")
                    db.session.rollback()  # 回滚失败的事务
                    # 不中断分析流程
            
            except Exception as e:
                logger.error(f"❌ 帧 {idx+1} 分析失败: {str(e)}")
                continue
        
        predict_time = time.time() - predict_start
        
        # 创建情绪时间轴
        timeline_data = create_emotion_timeline(analysis_results)
        
        # 计算统计数据
        statistics = calculate_emotion_statistics(analysis_results)
        
        logger.info(f"✅ 视频分析完成: {len(analysis_results)} 帧 (耗时: {predict_time:.2f}秒)")
        logger.info(f"📊 主导情绪: {statistics.get('dominant_emotion', 'N/A')}")
        logger.info(f"🔄 情绪流: {timeline_data.get('emotion_flow', 'N/A')}")
        
        # 💾 保存视频分析结果到数据库
        if current_username and analysis_results:
            try:
                # 保存视频分析结果
                video_result = VideoAnalysisResult(
                    username=current_username,
                    video_id=video_id,
                    total_frames=len(analysis_results),
                    duration_seconds=analysis_results[-1]['timestamp'] if analysis_results else 0,
                    dominant_emotion=statistics.get('dominant_emotion'),
                    dominant_emotion_cn=statistics.get('dominant_emotion_cn'),
                    avg_confidence=statistics.get('avg_confidence'),
                    emotion_distribution=statistics.get('emotion_counts'),
                    stability_level=statistics.get('emotion_stability', 'unknown'),
                    stability_score=statistics.get('stability_score', 0)
                )
                db.session.add(video_result)
                db.session.commit()
                logger.info(f"✅ 已保存视频分析结果: {video_id}")
                logger.info(f"ℹ️  情绪汇总已在每一帧分析时实时更新")
                
            except Exception as save_error:
                logger.error(f"保存视频分析结果失败: {save_error}")
                db.session.rollback()
        
        response_data = {
            'success': True,
            'video_id': video_id,
            'model_used': model_name.upper(),
            'total_frames': len(analysis_results),
            'frames': analysis_results,
            'timeline': timeline_data,
            'statistics': statistics,
            'performance': {
                'extraction_time': round(extract_time, 2),
                'prediction_time': round(predict_time, 2),
                'total_time': round(extract_time + predict_time, 2),
                'avg_time_per_frame': round(predict_time / len(analysis_results), 2) if analysis_results else 0
            }
        }
        
        logger.info(f"🔍 [DEBUG] 返回数据结构: success={response_data['success']}, video_id={response_data['video_id']}, total_frames={response_data['total_frames']}")
        logger.info(f"🔍 [DEBUG] timeline 类型: {type(response_data['timeline'])}, 键: {list(response_data['timeline'].keys()) if isinstance(response_data['timeline'], dict) else 'N/A'}")
        
        return jsonify(response_data)
    
    except Exception as e:
        logger.error(f"❌ 视频分析失败: {str(e)}")
        return jsonify({'error': f'视频分析失败: {str(e)}'}), 500


@app.route('/api/video/list', methods=['GET'])
@token_required
@admin_required
def list_videos():
    """获取已上传的视频列表"""
    try:
        videos = []
        
        if os.path.exists(video_processor.upload_folder):
            for filename in os.listdir(video_processor.upload_folder):
                filepath = os.path.join(video_processor.upload_folder, filename)
                
                if os.path.isfile(filepath) and allowed_video_file(filename):
                    stat = os.stat(filepath)
                    videos.append({
                        'video_id': filename,
                        'size': stat.st_size,
                        'upload_time': datetime.fromtimestamp(stat.st_ctime).isoformat()
                    })
        
        videos.sort(key=lambda x: x['upload_time'], reverse=True)
        
        return jsonify({
            'success': True,
            'videos': videos,
            'total': len(videos)
        })
    
    except Exception as e:
        logger.error(f"❌ 获取视频列表失败: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/video/delete/<video_id>', methods=['DELETE'])
@token_required
@admin_required
def delete_video(video_id):
    """删除指定的视频文件"""
    try:
        safe_id = Path(str(video_id)).name
        if safe_id != video_id or '..' in video_id or '/' in str(video_id) or '\\' in str(video_id):
            return jsonify({'error': '非法的 video_id'}), 400
        upload_root = Path(video_processor.upload_folder).resolve()
        video_path = (upload_root / safe_id).resolve()
        try:
            video_path.relative_to(upload_root)
        except ValueError:
            return jsonify({'error': '非法的 video_id'}), 400

        if not video_path.is_file():
            return jsonify({'error': '视频文件不存在'}), 404
        
        video_path.unlink()
        logger.info(f"🗑️  删除视频: {safe_id}")
        
        return jsonify({
            'success': True,
            'message': '视频删除成功'
        })
    
    except Exception as e:
        logger.error(f"❌ 删除视频失败: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ==================== 管理接口：用户与历史记录 CRUD (需要管理员权限) ====================
@app.route('/api/admin/users', methods=['GET'])
@token_required
@admin_required
def admin_list_users():
    """列出所有用户（分页可选）"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        q = User.query.order_by(User.created_at.desc())
        pag = q.paginate(page=page, per_page=per_page, error_out=False)
        users = [u.to_dict() for u in pag.items]
        return jsonify({'users': users, 'total': pag.total, 'page': page}), 200
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users', methods=['POST'])
@token_required
@admin_required
def admin_create_user():
    try:
        data = request.get_json() or {}
        username = (data.get('username') or '').strip()
        email = (data.get('email') or '').strip()
        password = data.get('password', '')
        role = data.get('role', 'user')
        if not username or not email:
            return jsonify({'error': 'username and email required'}), 400
        if not password or len(password) < 6:
            return jsonify({'error': 'password must be at least 6 characters'}), 400
        if User.query.filter((User.username == username) | (User.email == email)).first():
            return jsonify({'error': 'username or email already exists'}), 409
        u = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=role,
        )
        db.session.add(u)
        db.session.commit()
        return jsonify({'user': u.to_dict()}), 201
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['GET','PUT','DELETE'])
@token_required
@admin_required
def admin_user_detail(user_id):
    try:
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
    except Exception as e:
        logger.error(f"管理员操作用户失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/histories', methods=['GET'])
@token_required
@admin_required
def admin_list_histories():
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        q = PredictionHistory.query.order_by(PredictionHistory.created_at.desc())
        pag = q.paginate(page=page, per_page=per_page, error_out=False)
        items = [h.to_dict() for h in pag.items]
        
        # 🐛 调试：检查视频记录的 username 字段
        video_items = [item for item in items if item.get('input_type') == 'video']
        logger.info(f"📹 返回历史记录: 总数={len(items)}, 视频记录={len(video_items)}")
        if video_items:
            logger.info(f"📹 第一条视频记录: ID={video_items[0].get('id')}, username={video_items[0].get('username')}, input_type={video_items[0].get('input_type')}")
        
        return jsonify({'histories': items, 'total': pag.total, 'page': page}), 200
    except Exception as e:
        # 针对 sqlite 旧库缺列的情况，做一次性自修复：添加缺失列后重试
        msg = str(e)
        if 'no such column: prediction_history.username' in msg:
            try:
                logger.warning('检测到 prediction_history.username 缺失，正在自动添加该列...')
                db.session.execute(text("ALTER TABLE prediction_history ADD COLUMN username TEXT"))
                db.session.commit()
                # 添加完成后重试一次
                page = int(request.args.get('page', 1))
                per_page = int(request.args.get('per_page', 50))
                q = PredictionHistory.query.order_by(PredictionHistory.created_at.desc())
                pag = q.paginate(page=page, per_page=per_page, error_out=False)
                items = [h.to_dict() for h in pag.items]
                return jsonify({'histories': items, 'total': pag.total, 'page': page}), 200
            except Exception as e2:
                logger.error(f"自动添加 username 列失败: {e2}")
                return jsonify({'error': msg}), 500
        logger.error(f"获取历史记录失败: {e}")
        return jsonify({'error': msg}), 500


@app.route('/api/histories', methods=['GET'])
@token_required
def user_list_histories():
    """普通用户查看自己的历史记录（管理员仍可使用 /api/admin/histories 查看全部）。"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        current = getattr(request, 'current_user', None)
        if not current:
            return jsonify({'histories': [], 'total': 0, 'page': page}), 200

        username = current.get('username')
        if not username:
            return jsonify({'histories': [], 'total': 0, 'page': page}), 200

        q = PredictionHistory.query.filter_by(username=username).order_by(PredictionHistory.created_at.desc())
        pag = q.paginate(page=page, per_page=per_page, error_out=False)
        items = [h.to_dict() for h in pag.items]
        return jsonify({'histories': items, 'total': pag.total, 'page': page}), 200
    except Exception as e:
        msg = str(e)
        logger.error(f"用户获取历史记录失败: {e}")
        
        # 检查是否是缺少列的错误，尝试自动修复（SQLite）
        if 'no such column' in msg.lower() and 'prediction_history' in msg:
            try:
                from src.storage.database import _upgrade_prediction_history_add_username_if_needed
                logger.info("检测到数据库列缺失，尝试自动升级表结构...")
                _upgrade_prediction_history_add_username_if_needed()
                db.session.commit()
                
                # 重试查询
                q = PredictionHistory.query.filter_by(username=username).order_by(PredictionHistory.created_at.desc())
                pag = q.paginate(page=page, per_page=per_page, error_out=False)
                items = [h.to_dict() for h in pag.items]
                return jsonify({'histories': items, 'total': pag.total, 'page': page}), 200
            except Exception as e2:
                logger.error(f"自动升级表结构失败: {e2}")
        
        return jsonify({'error': msg}), 500


@app.route('/api/histories/<int:history_id>', methods=['DELETE'])
@token_required
def user_delete_history(history_id):
    """普通用户删除自己的历史记录"""
    try:
        current = getattr(request, 'current_user', None)
        if not current:
            return jsonify({'error': 'Unauthorized'}), 401
        
        username = current.get('username')
        h = PredictionHistory.query.get(history_id)
        if not h:
            return jsonify({'error': 'History not found'}), 404
        
        # 检查是否是自己的记录
        if h.username != username:
            return jsonify({'error': 'Forbidden: 只能删除自己的历史记录'}), 403
        
        db.session.delete(h)
        db.session.commit()
        return jsonify({'message': 'History deleted'}), 200
    except Exception as e:
        logger.error(f"用户删除历史记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/histories/<int:history_id>', methods=['GET','DELETE'])
@token_required
@admin_required
def admin_history_detail(history_id):
    try:
        h = PredictionHistory.query.get(history_id)
        if not h:
            return jsonify({'error': 'History not found'}), 404
        if request.method == 'GET':
            return jsonify({'history': h.to_dict()}), 200
        if request.method == 'DELETE':
            db.session.delete(h)
            db.session.commit()
            return jsonify({'message': 'History deleted'}), 200
    except Exception as e:
        logger.error(f"管理员操作历史记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/histories/clear', methods=['POST'])
@token_required
@admin_required
def admin_clear_histories():
    """管理员接口：清空所有预测历史，并在 SQLite 下重置自增序列（若存在）。"""
    try:
        # 删除所有记录
        deleted = PredictionHistory.query.delete()
        db.session.commit()

        # 如果使用 sqlite，重置 sqlite_sequence 中的条目以让 id 从 1 开始
        engine = db.engine
        if engine.dialect.name == 'sqlite':
            try:
                db.session.execute(text("DELETE FROM sqlite_sequence WHERE name='prediction_history'"))
                db.session.commit()
                logger.info('已重置 sqlite_sequence 中 prediction_history 的序列')
            except Exception as e:
                logger.warning(f'尝试重置 sqlite_sequence 失败: {e}')

        return jsonify({'deleted': deleted}), 200
    except Exception as e:
        logger.error(f"清空历史记录失败: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== 管理员 - 情绪日记管理 ====================
@app.route('/api/admin/journals', methods=['GET'])
@token_required
@admin_required
def admin_get_journals():
    """管理员接口：获取所有用户的情绪日记"""
    try:
        from src.storage.database import EmotionJournal
        
        limit = request.args.get('limit', 100, type=int)
        
        journals = EmotionJournal.query.order_by(
            EmotionJournal.created_at.desc()
        ).limit(limit).all()
        
        return jsonify({
            'journals': [j.to_dict() for j in journals]
        }), 200
        
    except Exception as e:
        logger.error(f"获取情绪日记失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/journals/<int:journal_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_journal(journal_id):
    """管理员接口：删除指定的情绪日记"""
    try:
        from src.storage.database import EmotionJournal
        
        journal = EmotionJournal.query.get(journal_id)
        if not journal:
            return jsonify({'error': 'Journal not found'}), 404
        
        db.session.delete(journal)
        db.session.commit()
        
        return jsonify({'message': 'Journal deleted'}), 200
        
    except Exception as e:
        logger.error(f"删除情绪日记失败: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 管理员 - 感恩记录管理 ====================
@app.route('/api/admin/gratitudes', methods=['GET'])
@token_required
@admin_required
def admin_get_gratitudes():
    """管理员接口：获取所有用户的感恩记录"""
    try:
        from src.storage.database import GratitudeRecord
        
        limit = request.args.get('limit', 100, type=int)
        
        gratitudes = GratitudeRecord.query.order_by(
            GratitudeRecord.created_at.desc()
        ).limit(limit).all()
        
        return jsonify({
            'gratitudes': [g.to_dict() for g in gratitudes]
        }), 200
        
    except Exception as e:
        logger.error(f"获取感恩记录失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/gratitudes/<int:gratitude_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_gratitude(gratitude_id):
    """管理员接口：删除指定的感恩记录"""
    try:
        from src.storage.database import GratitudeRecord
        
        gratitude = GratitudeRecord.query.get(gratitude_id)
        if not gratitude:
            return jsonify({'error': 'Gratitude record not found'}), 404
        
        db.session.delete(gratitude)
        db.session.commit()
        
        return jsonify({'message': 'Gratitude record deleted'}), 200
        
    except Exception as e:
        logger.error(f"删除感恩记录失败: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 管理员 - 情绪汇总管理 ====================
@app.route('/api/admin/emotion-summaries', methods=['GET'])
@token_required
@admin_required
def admin_get_emotion_summaries():
    """管理员接口：获取所有用户的情绪汇总"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        summaries = UserEmotionSummary.query.order_by(
            UserEmotionSummary.summary_date.desc()
        ).limit(limit).all()
        
        return jsonify({
            'summaries': [s.to_dict() for s in summaries]
        }), 200
        
    except Exception as e:
        logger.error(f"获取情绪汇总失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/emotion-summaries/<int:summary_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_emotion_summary(summary_id):
    """管理员接口：删除指定的情绪汇总"""
    try:
        summary = UserEmotionSummary.query.get(summary_id)
        if not summary:
            return jsonify({'error': 'Summary not found'}), 404
        
        db.session.delete(summary)
        db.session.commit()
        
        return jsonify({'message': 'Summary deleted'}), 200
        
    except Exception as e:
        logger.error(f"删除情绪汇总失败: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 管理员 - 健康评估管理 ====================
@app.route('/api/admin/health-assessments', methods=['GET'])
@token_required
@admin_required
def admin_get_health_assessments():
    """管理员接口：获取所有用户的健康评估"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        assessments = HealthAssessment.query.order_by(
            HealthAssessment.assessment_date.desc()
        ).limit(limit).all()
        
        return jsonify({
            'assessments': [a.to_dict() for a in assessments]
        }), 200
        
    except Exception as e:
        logger.error(f"获取健康评估失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/health-assessments/<int:assessment_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_health_assessment(assessment_id):
    """管理员接口：删除指定的健康评估"""
    try:
        assessment = HealthAssessment.query.get(assessment_id)
        if not assessment:
            return jsonify({'error': 'Assessment not found'}), 404
        
        db.session.delete(assessment)
        db.session.commit()
        
        return jsonify({'message': 'Assessment deleted'}), 200
        
    except Exception as e:
        logger.error(f"删除健康评估失败: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 管理员 - 视频分析管理 ====================
@app.route('/api/admin/video-analyses', methods=['GET'])
@token_required
@admin_required
def admin_get_video_analyses():
    """管理员接口：获取所有用户的视频分析结果"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        analyses = VideoAnalysisResult.query.order_by(
            VideoAnalysisResult.created_at.desc()
        ).limit(limit).all()
        
        return jsonify({
            'analyses': [a.to_dict() for a in analyses]
        }), 200
        
    except Exception as e:
        logger.error(f"获取视频分析失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/video-analyses/<int:analysis_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_video_analysis(analysis_id):
    """管理员接口：删除指定的视频分析结果"""
    try:
        analysis = VideoAnalysisResult.query.get(analysis_id)
        if not analysis:
            return jsonify({'error': 'Analysis not found'}), 404
        
        db.session.delete(analysis)
        db.session.commit()
        
        return jsonify({'message': 'Analysis deleted'}), 200
        
    except Exception as e:
        logger.error(f"删除视频分析失败: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== 静态文件服务 ====================
@app.route('/api/uploads/<path:filename>')
@token_required_or_query
def serve_uploaded_file(filename):
    """提供上传文件的访问服务（需登录；所有者或管理员可读）"""
    try:
        from flask import send_from_directory
        target = _resolve_upload_file(filename)
        if target is None:
            return jsonify({'error': 'File not found'}), 404
        rel = target.relative_to(UPLOAD_ROOT).as_posix()
        if not _user_can_access_upload(rel, request.current_user):
            return jsonify({'error': 'Forbidden'}), 403
        return send_from_directory(str(UPLOAD_ROOT), rel)
    except Exception as e:
        logger.error(f"访问文件失败: {e}")
        return jsonify({'error': 'File not found'}), 404


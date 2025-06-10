import os
import re
import json
import time
import random
import string
import logging
import secrets
import base64
import io
import hashlib
from typing import Dict, List, Any, Optional, Union, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

import requests
from flask import Flask, request, Response, jsonify, stream_with_context, render_template_string, session, redirect, url_for

# 导入配置管理器和适配器
from config_manager import config_manager, ServiceProvider, ProviderType, UserKey, AdminConfig
from fal_adapter import FalAIAdapter

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# 权限验证装饰器
def verify_permission(required_level: str = "guest"):
    """权限验证装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 获取端点权限配置
            endpoint_permissions = config_manager.get_endpoint_permissions()
            endpoint = request.endpoint or request.path
            
            # 检查端点权限要求
            actual_required_level = endpoint_permissions.get(endpoint, required_level)
            
            # 访客级别不需要验证
            if actual_required_level == "guest":
                return f(*args, **kwargs)
            
            # 获取授权信息
            auth_header = request.headers.get("Authorization", "")
            api_key = None
            
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]
            elif auth_header.startswith("Key "):
                api_key = auth_header[4:]
            
            # URL参数中的key
            if not api_key:
                api_key = request.args.get("key") or request.form.get("key")
            
            if not api_key:
                return jsonify({"error": "Unauthorized: API key required"}), 401
            
            # 验证管理员Key
            system_config = config_manager.get_system_config()
            if api_key == system_config.api_key and system_config.api_key:
                # 管理员权限，允许所有操作
                return f(*args, **kwargs)
            
            # 验证用户Key
            user_key = config_manager.get_user_key_by_key(api_key)
            if not user_key or not user_key.enabled:
                return jsonify({"error": "Unauthorized: Invalid API key"}), 401
            
            # 检查权限等级
            if actual_required_level == "admin" and user_key.level != "admin":
                return jsonify({"error": "Forbidden: Admin access required"}), 403
            
            if actual_required_level == "user" and user_key.level not in ["user", "admin"]:
                return jsonify({"error": "Forbidden: User access required"}), 403
            
            # 更新使用记录
            config_manager.update_user_key_usage(api_key)
            
            return f(*args, **kwargs)
        
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator

def require_admin_auth(f):
    """管理员认证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_authenticated' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# 辅助函数（保持原有功能）
def get_env_with_fallback(key: str, default: str = "") -> str:
    """获取配置值，优先级：Redis/SQLite > 环境变量 > 默认值"""
    return config_manager.get_env_with_fallback(key, default)

def get_env_bool(key: str, default: bool = False) -> bool:
    """获取布尔类型配置值"""
    value = get_env_with_fallback(key, str(default)).lower()
    return value in ("true", "1", "yes", "y", "t")

def get_env_int(key: str, default: int) -> int:
    """获取整数类型配置值"""
    try:
        return int(get_env_with_fallback(key, str(default)))
    except ValueError:
        return default

# 保持原有的辅助函数
def contains_chinese(text: str) -> bool:
    """检查文本是否包含中文字符"""
    pattern = re.compile(r'[\u4e00-\u9fff]')
    return bool(pattern.search(text))

def match_resolution(text: str) -> str:
    """从文本中匹配分辨率或宽高比"""
    # 直接匹配常见分辨率格式
    resolution_pattern = re.compile(r'\b(\d+)[xX×*](\d+)\b')
    match = resolution_pattern.search(text)
    if match:
        width, height = match.groups()
        logger.info(f"检测到分辨率: {width}x{height}")
        return f"{width}x{height}"
    
    # 预定义的分辨率
    specific_resolutions = [
        "1024x1024", "512x1024", "768x512", "768x1024", "1024x576", "576x1024"
    ]
    
    # 检查特定分辨率关键词
    for resolution in specific_resolutions:
        if re.search(r'\b' + resolution + r'\b', text):
            logger.info(f"匹配到预定义分辨率: {resolution}")
            return resolution
    
    # 宽高比映射
    aspect_ratios = {
        "1:1": "1024x1024",
        "1:2": "512x1024", 
        "2:1": "1024x512",
        "3:2": "768x512",
        "2:3": "512x768",
        "3:4": "768x1024",
        "4:3": "1024x768",
        "16:9": "1024x576",
        "9:16": "576x1024"
    }
    
    # 检查宽高比
    for ratio, resolution in aspect_ratios.items():
        if re.search(r'\b' + ratio + r'\b', text):
            logger.info(f"匹配到宽高比 {ratio}, 使用分辨率: {resolution}")
            return resolution
    
    # 检查关键词
    if re.search(r'\b(square|正方形)\b', text, re.IGNORECASE):
        return "1024x1024"
    elif re.search(r'\b(landscape|横向|横屏)\b', text, re.IGNORECASE):
        return "1024x768"
    elif re.search(r'\b(portrait|纵向|竖屏)\b', text, re.IGNORECASE):
        return "768x1024"
    elif re.search(r'\b(wide|宽屏)\b', text, re.IGNORECASE):
        return "1024x576"
    
    logger.info("未检测到特定分辨率，使用默认值: 1024x1024")
    return "1024x1024"

def moderate_check(text: str) -> bool:
    """检查文本是否包含被禁止的关键词"""
    system_config = config_manager.get_system_config()
    banned_words = system_config.banned_keywords.split(",") if system_config.banned_keywords else []
    text_lower = text.lower()
    
    for word in banned_words:
        if word and word.strip() and word.strip().lower() in text_lower:
            logger.info(f"检测到禁止关键词: {word}")
            return True
    
    return False

def generate_random_slug(length: int = 3) -> str:
    """生成随机短链接标识"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def generate_short_url(long_url: str) -> str:
    """生成短链接"""
    shortlink_config = config_manager.get_shortlink_config()
    
    if not shortlink_config.enabled:
        return long_url
    
    if len(long_url) < 30:
        return long_url
    
    if not shortlink_config.base_url or not shortlink_config.api_key:
        return long_url
    
    slug = generate_random_slug()
    api_url = f"{shortlink_config.base_url}/api/link/create"
    
    try:
        response = requests.post(
            api_url,
            json={"url": long_url, "slug": slug},
            headers={
                "Authorization": f"Bearer {shortlink_config.api_key}",
                "Content-Type": "application/json"
            },
            timeout=5
        )
        
        if response.status_code in (200, 201):
            return f"{shortlink_config.base_url}{slug}"
        
        logger.error(f"短链接API错误响应: {response.text}")
    except Exception as e:
        logger.error(f"生成短链接失败: {e}")
    
    return long_url

def upload_to_lsky_pro(image_data: Union[str, bytes]) -> Optional[str]:
    """上传图片到蓝空图床"""
    hosting_config = config_manager.get_image_hosting_config()
    
    if not hosting_config.enabled:
        return None
    
    if not hosting_config.lsky_url or not hosting_config.token:
        logger.error("蓝空图床配置不完整")
        return None
    
    try:
        # 准备图片数据
        image_content = None
        
        # 如果是URL，下载图片
        if isinstance(image_data, str) and (image_data.startswith('http://') or image_data.startswith('https://')):
            logger.info(f"从URL下载图片: {image_data}")
            image_response = requests.get(image_data, timeout=10)
            if image_response.status_code != 200:
                logger.error(f"下载图片失败: {image_response.status_code}")
                return None
            image_content = image_response.content
        
        # 如果是base64编码的图片
        elif isinstance(image_data, str) and image_data.startswith('data:image'):
            logger.info("处理base64编码的图片")
            image_data = image_data.split(',', 1)[1] if ',' in image_data else image_data
            try:
                image_content = base64.b64decode(image_data)
            except Exception as e:
                logger.error(f"解码base64图片失败: {e}")
                return None
        
        # 如果是二进制数据
        elif isinstance(image_data, bytes):
            logger.info("处理二进制图片数据")
            image_content = image_data
        
        else:
            logger.error(f"不支持的图片数据格式: {type(image_data)}")
            return None
        
        # 准备上传到蓝空图床
        upload_url = f"{hosting_config.lsky_url.rstrip('/')}/api/v1/upload"
        
        files = {
            'file': ('image.png', image_content, 'image/png')
        }
        
        headers = {
            'Authorization': f'Bearer {hosting_config.token}'
        }
        
        logger.info(f"上传图片到蓝空图床: {upload_url}")
        upload_response = requests.post(
            upload_url,
            files=files,
            headers=headers,
            timeout=30
        )
        
        if upload_response.status_code != 200:
            logger.error(f"上传到蓝空图床失败: {upload_response.status_code}, {upload_response.text}")
            return None
        
        # 解析响应
        try:
            result = upload_response.json()
            if result.get("status") and "data" in result and "links" in result["data"]:
                lsky_url = result["data"]["links"].get("url")
                if lsky_url:
                    logger.info(f"上传到蓝空图床成功: {lsky_url}")
                    return lsky_url
            
            logger.error(f"解析蓝空图床响应失败: {result}")
        except Exception as e:
            logger.error(f"解析蓝空图床响应失败: {e}")
        
    except Exception as e:
        logger.error(f"上传到蓝空图床失败: {e}")
    
    return None

def generate_image_prompt(api_key: str, text: str) -> str:
    """使用LLM生成图像提示"""
    ai_config = config_manager.get_ai_prompt_config()
    
    if not ai_config.enabled:
        return text
    
    messages = [
        {
            "role": "system",
            "content": ai_config.system_prompt
        },
        {
            "role": "user",
            "content": text
        }
    ]
    
    try:
        response = requests.post(
            ai_config.api_url,
            json={
                "model": ai_config.model,
                "messages": messages
            },
            headers={
                "Authorization": f"Bearer {ai_config.api_key}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"生成图像提示失败: {e}")
    
    return text

def extract_base64_image(response_data: Dict) -> Optional[str]:
    """从API响应中提取base64编码的图片"""
    try:
        # 检查常见的base64图片字段
        if "images" in response_data and isinstance(response_data["images"], list) and len(response_data["images"]) > 0:
            if isinstance(response_data["images"][0], str):
                base64_data = response_data["images"][0]
                if base64_data.startswith('data:image'):
                    return base64_data
                else:
                    return f"data:image/png;base64,{base64_data}"
            
            elif isinstance(response_data["images"][0], dict):
                if "b64_json" in response_data["images"][0]:
                    return f"data:image/png;base64,{response_data['images'][0]['b64_json']}"
                elif "data" in response_data["images"][0]:
                    data = response_data["images"][0]["data"]
                    if isinstance(data, str):
                        if data.startswith('data:image'):
                            return data
                        else:
                            return f"data:image/png;base64,{data}"
        
        if "data" in response_data and isinstance(response_data["data"], list) and len(response_data["data"]) > 0:
            if "b64_json" in response_data["data"][0]:
                return f"data:image/png;base64,{response_data['data'][0]['b64_json']}"
            elif "base64" in response_data["data"][0]:
                return f"data:image/png;base64,{response_data['data'][0]['base64']}"
        
        if "b64_json" in response_data:
            return f"data:image/png;base64,{response_data['b64_json']}"
        elif "base64" in response_data:
            return f"data:image/png;base64,{response_data['base64']}"
        
        logger.error(f"未找到base64图片数据: {list(response_data.keys())}")
        return None
    
    except Exception as e:
        logger.error(f"提取base64图片失败: {e}")
        return None

def extract_image_url(response_data: Dict) -> Optional[str]:
    """从API响应中提取图片URL"""
    try:
        if "images" in response_data and isinstance(response_data["images"], list) and len(response_data["images"]) > 0:
            if isinstance(response_data["images"][0], str) and (response_data["images"][0].startswith('http://') or response_data["images"][0].startswith('https://')):
                return response_data["images"][0]
            
            elif isinstance(response_data["images"][0], dict):
                if "url" in response_data["images"][0]:
                    return response_data["images"][0]["url"]
                elif "image_url" in response_data["images"][0]:
                    return response_data["images"][0]["image_url"]
        
        if "data" in response_data and isinstance(response_data["data"], list) and len(response_data["data"]) > 0:
            if "url" in response_data["data"][0]:
                return response_data["data"][0]["url"]
            elif "image_url" in response_data["data"][0]:
                return response_data["data"][0]["image_url"]
        
        if "url" in response_data:
            return response_data["url"]
        elif "image_url" in response_data:
            return response_data["image_url"]
        
        logger.error(f"未找到图片URL: {list(response_data.keys())}")
        return None
    
    except Exception as e:
        logger.error(f"提取图片URL失败: {e}")
        return None

def extract_seed_from_text(text: str) -> tuple[str, Optional[int]]:
    """从文本中提取种子值"""
    pattern = re.compile(r'\bseed:(\d+)\b')
    match = pattern.search(text)
    
    if not match:
        return text, None
    
    seed = int(match.group(1))
    cleaned_text = pattern.sub('', text).strip()
    
    logger.info(f"检测到种子设置: {seed}")
    return cleaned_text, seed

def extract_seed_from_response(response_data: Dict) -> Optional[int]:
    """从API响应中提取种子值"""
    try:
        if "meta" in response_data:
            meta = response_data["meta"]
            if isinstance(meta, dict) and "seed" in meta:
                return int(meta["seed"])
        
        if "images" in response_data and isinstance(response_data["images"], list) and len(response_data["images"]) > 0:
            if isinstance(response_data["images"][0], dict):
                if "seed" in response_data["images"][0]:
                    return int(response_data["images"][0]["seed"])
                elif "meta" in response_data["images"][0] and isinstance(response_data["images"][0]["meta"], dict):
                    if "seed" in response_data["images"][0]["meta"]:
                        return int(response_data["images"][0]["meta"]["seed"])
        
        if "seed" in response_data:
            return int(response_data["seed"])
        
        logger.warning(f"未找到种子值: {list(response_data.keys())}")
        return None
    
    except Exception as e:
        logger.error(f"提取种子值失败: {e}")
        return None

def call_provider_api(provider: ServiceProvider, model: str, prompt: str, options: Dict) -> List[str]:
    """调用服务商API生成图像"""
    if provider.provider_type == ProviderType.FAL_AI:
        # 使用Fal.ai适配器
        fal_adapter = FalAIAdapter(provider.api_keys)
        return fal_adapter.call_fal_api(prompt, model, options)
    
    elif provider.provider_type == ProviderType.OPENAI_ADAPTER:
        # OpenAI适配器类型
        url = f"{provider.base_url.rstrip('/')}/images/generations"
        headers = {
            "Authorization": f"Bearer {provider.api_keys[0]}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "prompt": prompt,
            "size": options.get("size", "1024x1024"),
            "n": options.get("n", 1),
            "response_format": options.get("response_format", "url")
        }
        
        if "seed" in options:
            data["seed"] = options["seed"]
        
        response = requests.post(url, headers=headers, json=data, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            if "data" in result:
                return [item["url"] for item in result["data"] if "url" in item]
        
        raise ValueError(f"OpenAI适配器调用失败: {response.text}")
    
    else:
        # 本项目对接类型 - 使用原有逻辑
        return call_native_api(provider, model, prompt, options)

def call_native_api(provider: ServiceProvider, model: str, prompt: str, options: Dict) -> List[str]:
    """调用本项目对接类型的API"""
    # 根据模型选择API端点
    if model == "Kwai-Kolors/Kolors":
        url = f"{provider.base_url.rstrip('/')}/v1/images/generations"
        data = {
            "model": model,
            "prompt": prompt,
            "image_size": options.get("size", "1024x1024"),
            "batch_size": 1,
            "num_inference_steps": 20,
            "guidance_scale": 7.5
        }
    elif "flux" in model.lower():
        url = f"{provider.base_url.rstrip('/')}/v1/image/generations"
        data = {
            "model": model,
            "prompt": prompt,
            "image_size": options.get("size", "1024x1024"),
            "num_inference_steps": 20,
            "prompt_enhancement": True
        }
    else:
        url = f"{provider.base_url.rstrip('/')}/v1/{model}/text-to-image"
        data = {
            "prompt": prompt,
            "image_size": options.get("size", "1024x1024"),
            "num_inference_steps": 20
        }
    
    if "seed" in options:
        data["seed"] = options["seed"]
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {provider.api_keys[0]}"
    }
    
    response = requests.post(url, json=data, headers=headers, timeout=60)
    
    if response.status_code == 200:
        result = response.json()
        
        # 提取图片URL
        image_url = extract_image_url(result)
        if image_url:
            return [image_url]
        
        # 提取base64图片
        base64_data = extract_base64_image(result)
        if base64_data:
            return [base64_data]
        
        raise ValueError("未找到图片数据")
    
    raise ValueError(f"API调用失败: {response.text}")

def process_image_response(response_data: Union[List[str], str], prompt: str) -> Tuple[bool, str, Optional[str]]:
    """处理图像API的响应"""
    try:
        # 如果是字符串列表，取第一个
        if isinstance(response_data, list) and len(response_data) > 0:
            image_data = response_data[0]
        elif isinstance(response_data, str):
            image_data = response_data
        else:
            logger.error(f"无效的响应数据类型: {type(response_data)}")
            return False, "无效的响应数据", None
        
        safe_prompt = prompt.replace("\n", " ")
        
        # 处理URL类型的图片
        if image_data.startswith('http://') or image_data.startswith('https://'):
            logger.info(f"找到图片URL: {image_data}")
            
            short_url = generate_short_url(image_data)
            lsky_url = upload_to_lsky_pro(image_data)
            
            if lsky_url:
                return True, lsky_url, lsky_url
            else:
                return True, short_url, short_url
        
        # 处理base64类型的图片
        elif image_data.startswith('data:image'):
            logger.info("找到base64图片数据")
            
            hosting_config = config_manager.get_image_hosting_config()
            if hosting_config.enabled:
                lsky_url = upload_to_lsky_pro(image_data)
                
                if lsky_url:
                    return True, lsky_url, lsky_url
            
            return True, image_data, image_data
        
        else:
            logger.error(f"未识别的图片数据格式: {image_data[:100]}...")
            return False, "未识别的图片格式", None
    
    except Exception as e:
        logger.error(f"处理图片响应失败: {e}")
        return False, f"处理响应时出错: {str(e)}", None

def get_all_supported_models() -> List[str]:
    """获取所有支持的模型列表"""
    providers = config_manager.get_all_providers()
    all_models = set()
    
    for provider in providers:
        if provider.enabled:
            all_models.update(provider.models)
    
    # 如果没有配置的服务商，返回默认模型
    if not all_models:
        for provider_type in ProviderType:
            all_models.update(config_manager.get_default_models_for_type(provider_type))
    
    return list(all_models)

def find_provider_for_model(model: str) -> Optional[ServiceProvider]:
    """根据模型名称查找支持该模型的服务商"""
    providers = config_manager.get_all_providers()
    
    # 只考虑启用的服务商
    enabled_providers = [p for p in providers if p.enabled]
    
    # 首先检查是否有服务商明确支持该模型
    for provider in enabled_providers:
        if model in provider.models:
            return provider
    
    # 如果没有找到，返回第一个启用的服务商（如果有）
    return enabled_providers[0] if enabled_providers else None

# 管理员登录页面
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        admin_config = config_manager.get_admin_config()
        
        if username == admin_config.username and password == admin_config.password:
            session['admin_authenticated'] = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '用户名或密码错误'})
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@require_admin_auth
def admin_dashboard():
    return render_template_string(ADMIN_TEMPLATE)

# 管理员API - 获取状态
@app.route('/admin/api/status')
@require_admin_auth
def get_admin_status():
    return jsonify(config_manager.get_config_status())

# 管理员API - 管理员配置
@app.route('/admin/api/admin-config', methods=['GET'])
@require_admin_auth
def get_admin_config():
    config = config_manager.get_admin_config()
    return jsonify({
        'username': config.username,
        'password': config.password
    })

@app.route('/admin/api/admin-config', methods=['POST'])
@require_admin_auth
def set_admin_config():
    data = request.get_json()
    config = AdminConfig(
        username=data.get('username', 'admin'),
        password=data.get('password', 'admin123')
    )
    config_manager.set_admin_config(config)
    return jsonify({'success': True})

# 管理员API - 用户Key管理
@app.route('/admin/api/user-keys', methods=['GET'])
@require_admin_auth
def get_user_keys():
    user_keys = config_manager.get_all_user_keys()
    return jsonify([{
        'id': uk.id,
        'name': uk.name,
        'key': uk.key,
        'level': uk.level,
        'enabled': uk.enabled,
        'created_at': uk.created_at,
        'last_used': uk.last_used,
        'usage_count': uk.usage_count
    } for uk in user_keys])

@app.route('/admin/api/user-keys', methods=['POST'])
@require_admin_auth
def add_user_key():
    data = request.get_json()
    
    # 生成唯一ID和Key
    key_id = hashlib.md5(f"{data['name']}{datetime.now().isoformat()}".encode()).hexdigest()[:8]
    api_key = f"sk-{secrets.token_urlsafe(32)}"
    
    user_key = UserKey(
        id=key_id,
        name=data['name'],
        key=api_key,
        level=data.get('level', 'user'),
        enabled=data.get('enabled', True)
    )
    
    if config_manager.add_user_key(user_key):
        return jsonify({'success': True, 'key': api_key})
    else:
        return jsonify({'success': False, 'message': '添加用户Key失败'})

@app.route('/admin/api/user-keys/<key_id>', methods=['PUT'])
@require_admin_auth
def update_user_key(key_id):
    data = request.get_json()
    user_key = config_manager.get_user_key(key_id)
    
    if not user_key:
        return jsonify({'success': False, 'message': '用户Key不存在'})
    
    user_key.name = data.get('name', user_key.name)
    user_key.level = data.get('level', user_key.level)
    user_key.enabled = data.get('enabled', user_key.enabled)
    
    if config_manager.add_user_key(user_key):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': '更新用户Key失败'})

@app.route('/admin/api/user-keys/<key_id>', methods=['DELETE'])
@require_admin_auth
def delete_user_key(key_id):
    if config_manager.delete_user_key(key_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': '删除用户Key失败'})

# 管理员API - 权限配置
@app.route('/admin/api/permissions', methods=['GET'])
@require_admin_auth
def get_permissions():
    return jsonify(config_manager.get_endpoint_permissions())

@app.route('/admin/api/permissions', methods=['POST'])
@require_admin_auth
def set_permissions():
    data = request.get_json()
    config_manager.set_endpoint_permissions(data)
    return jsonify({'success': True})

# 服务商管理API（与之前类似，但添加权限验证）
@app.route('/admin/api/providers', methods=['GET'])
@require_admin_auth
def get_providers():
    providers = config_manager.get_all_providers()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'provider_type': p.provider_type.value,
        'base_url': p.base_url,
        'api_keys_count': len(p.api_keys),
        'models_count': len(p.models),
        'enabled': p.enabled,
        'created_at': p.created_at
    } for p in providers])

@app.route('/admin/api/providers', methods=['POST'])
@require_admin_auth
def add_provider():
    data = request.get_json()
    
    # 生成唯一ID
    provider_id = hashlib.md5(f"{data['name']}{datetime.now().isoformat()}".encode()).hexdigest()[:8]
    
    # 处理base_url
    base_url = data['base_url'].rstrip('/')
    if data['provider_type'] == 'openai_adapter' and not base_url.endswith('/v1'):
        if '/' not in base_url.split('://', 1)[1]:  # 只有域名
            base_url += '/v1'
    
    # 获取默认模型
    provider_type = ProviderType(data['provider_type'])
    default_models = config_manager.get_default_models_for_type(provider_type)
    
    # 如果用户没有指定模型，使用默认模型
    user_models = data['models'].split(',') if data['models'] else []
    final_models = user_models if user_models else default_models
    
    provider = ServiceProvider(
        id=provider_id,
        name=data['name'],
        provider_type=provider_type,
        base_url=base_url,
        api_keys=data['api_keys'].split(',') if data['api_keys'] else [],
        models=final_models,
        enabled=data.get('enabled', True)
    )
    
    if config_manager.add_provider(provider):
        return jsonify({'success': True, 'provider_id': provider_id})
    else:
        return jsonify({'success': False, 'message': '添加服务商失败'})

# 主要API路由
@app.route("/v1/models", methods=["GET"])
@verify_permission("guest")  # 默认访客级别
def list_models():
    """列出支持的模型"""
    all_models = get_all_supported_models()
    models_data = {
        "object": "list",
        "data": [{"id": model, "object": "model"} for model in all_models]
    }
    return jsonify(models_data)

@app.route("/v1/images/generations", methods=["POST"])
@verify_permission("user")  # 默认用户级别
def openai_images():
    """OpenAI兼容的图像生成接口"""
    data = request.json
    if not data:
        return jsonify({
            "error": {
                "message": "Missing or invalid request body",
                "type": "invalid_request_error"
            }
        }), 400
    
    prompt = data.get('prompt', '')
    if not prompt:
        return jsonify({
            "error": {
                "message": "prompt is required",
                "type": "invalid_request_error"
            }
        }), 400
    
    model = data.get('model', 'flux-dev')
    size = data.get('size', '1024x1024')
    
    # 内容审核
    if moderate_check(prompt):
        return jsonify({
            "error": {
                "message": "Content policy violation",
                "type": "policy_violation"
            }
        }), 400
    
    # 查找支持该模型的服务商
    provider = find_provider_for_model(model)
    if not provider:
        return jsonify({
            "error": {
                "message": f"Model '{model}' not found",
                "type": "invalid_request_error"
            }
        }), 400
    
    try:
        # 生成图像提示
        enhanced_prompt = generate_image_prompt(provider.api_keys[0] if provider.api_keys else "", prompt)
        
        # 调用API生成图像
        options = {"size": size, "n": 1, "num_images": 1}
        image_urls = call_provider_api(provider, model, enhanced_prompt, options)
        
        # 构建OpenAI格式响应
        data_list = [{"url": url} for url in image_urls]
        
        response = {
            "created": int(time.time()),
            "data": data_list
        }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"图像生成失败: {str(e)}")
        return jsonify({
            "error": {
                "message": f"Image generation failed: {str(e)}",
                "type": "server_error"
            }
        }), 500

@app.route("/gen", methods=["GET", "POST"])
@verify_permission("user")  # 默认用户级别
def simple_gen():
    """简单的图像生成接口，支持GET和POST，只支持1:1且一次一张"""
    if request.method == "GET":
        prompt = request.args.get('prompt', '').strip()
        model = request.args.get('model', '')
    else:
        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model = data.get('model', '')
    
    if not prompt:
        return jsonify({"error": "prompt parameter is required"}), 400
    
    # 内容审核
    if moderate_check(prompt):
        return jsonify({"error": "Content policy violation"}), 400
    
    # 如果没有指定模型，使用第一个可用模型
    if not model:
        all_models = get_all_supported_models()
        if not all_models:
            return jsonify({"error": "No models available"}), 500
        model = all_models[0]
    
    # 查找支持该模型的服务商
    provider = find_provider_for_model(model)
    if not provider:
        return jsonify({"error": f"Model '{model}' not found"}), 400
    
    try:
        # 提取种子值
        prompt, seed = extract_seed_from_text(prompt)
        
        # 生成图像提示
        enhanced_prompt = generate_image_prompt(provider.api_keys[0] if provider.api_keys else "", prompt)
        
        # 固定使用1:1比例
        options = {"size": "1024x1024", "n": 1, "num_images": 1}
        if seed is not None:
            options["seed"] = seed
        
        # 调用API生成图像
        image_urls = call_provider_api(provider, model, enhanced_prompt, options)
        
        # 处理响应
        success, image_url, final_url = process_image_response(image_urls, enhanced_prompt)
        
        if success:
            return jsonify({
                "success": True,
                "prompt": enhanced_prompt,
                "model": model,
                "size": "1024x1024",
                "image_url": final_url,
                "seed": seed
            })
        else:
            return jsonify({"error": f"Image generation failed: {image_url}"}), 500
            
    except Exception as e:
        logger.error(f"图像生成失败: {str(e)}")
        return jsonify({"error": f"Image generation failed: {str(e)}"}), 500

@app.route("/v1/chat/completions", methods=["POST"])
@verify_permission("user")  # 默认用户级别
def handle_request():
    """处理图像生成请求（保持原有功能，但限制为一次一张）"""
    try:
        body = request.json
        
        if not body or "model" not in body or "messages" not in body or not body["messages"]:
            return jsonify({"error": "Bad Request: Missing required fields"}), 400
        
        if "janus" in body["model"].lower():
            return jsonify({"error": f"该模型已下架: {body['model']}"}), 410
        
        # 构建完整上下文
        full_context = ""
        for message in body["messages"]:
            if message["role"] != "assistant":
                full_context += message["content"] + "\n\n"
        context = full_context.strip()
        
        # 强制限制为1张图片
        context, seed = extract_seed_from_text(context)
        final_count = 1  # 强制限制
        
        # 内容审核
        if moderate_check(context):
            nsfw_response = "Warning: Prohibited Content Detected! 🚫\n\nYour request contains banned keywords. Please check the content and try again.\n\n-----------------------\n\n警告：请求包含被禁止的关键词，请检查后重试！⚠️"
            
            if body.get("stream", False):
                def generate():
                    unique_id = int(time.time() * 1000)
                    current_timestamp = int(time.time())
                    
                    initial_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None, "logprobs": None}],
                        "system_fingerprint": "fp_default"
                    }
                    yield f"data: {json.dumps(initial_payload)}\n\n"
                    
                    for chunk in nsfw_response:
                        payload = {
                            "id": unique_id,
                            "object": "chat.completion.chunk",
                            "created": current_timestamp,
                            "model": body["model"],
                            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None, "logprobs": None}],
                            "system_fingerprint": "fp_default"
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                    
                    end_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop", "logprobs": None}],
                        "system_fingerprint": "fp_default"
                    }
                    yield f"data: {json.dumps(end_payload)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type="text/event-stream")
            
            else:
                response_payload = {
                    "id": int(time.time() * 1000),
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": nsfw_response}, "logprobs": None, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": len(context), "completion_tokens": len(nsfw_response), "total_tokens": len(context) + len(nsfw_response)}
                }
                return jsonify(response_payload)
        
        # 查找支持该模型的服务商
        provider = find_provider_for_model(body["model"])
        if not provider:
            return jsonify({"error": f"未找到支持该模型的服务商: {body['model']}"}), 404
        
        # 生成图像提示
        prompt = generate_image_prompt(provider.api_keys[0] if provider.api_keys else "", context)
        safe_prompt = prompt.replace("\n", " ")
        
        image_size = match_resolution(context)
        logger.info(f"用户请求的图像尺寸: {image_size}")
        
        unique_id = int(time.time() * 1000)
        current_timestamp = int(time.time())
        
        # 流式响应
        if body.get("stream", False):
            def generate():
                initial_payload = {
                    "id": unique_id,
                    "object": "chat.completion.chunk",
                    "created": current_timestamp,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None, "logprobs": None}],
                    "system_fingerprint": "fp_default"
                }
                yield f"data: {json.dumps(initial_payload)}\n\n"
                
                time.sleep(0.1)
                
                prompt_payload = {
                    "id": unique_id,
                    "object": "chat.completion.chunk",
                    "created": current_timestamp,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": f"\`\`\`\n{{\n  \"prompt\":\"{safe_prompt}\",\n  \"count\":{final_count}\n}}\n\`\`\`\n"}}],
                    "finish_reason": None
                }
                yield f"data: {json.dumps(prompt_payload)}\n\n"
                
                time.sleep(0.5)
                
                task_payload = {
                    "id": unique_id,
                    "object": "chat.completion.chunk",
                    "created": current_timestamp,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": f"> 正在生成 {final_count} 张图片..."}}],
                    "finish_reason": None
                }
                yield f"data: {json.dumps(task_payload)}\n\n"
                
                time.sleep(0.5)
                
                try:
                    options = {
                        "size": image_size,
                        "n": 1,
                        "num_images": 1
                    }
                    
                    if seed is not None:
                        options["seed"] = seed
                    
                    logger.info(f"开始生成图片")
                    image_urls = call_provider_api(provider, body["model"], prompt, options)
                    
                    success, image_text, _ = process_image_response(image_urls, prompt)
                    
                    if success:
                        image_content = f"\n\n图片生成完成 ✅\n\n![image|{safe_prompt}]({image_text})"
                    else:
                        image_content = f"\n\n图片生成失败 ❌ - {image_text}"
                    
                    image_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {"content": image_content}}],
                        "finish_reason": None
                    }
                    yield f"data: {json.dumps(image_payload)}\n\n"
                    
                except Exception as e:
                    logger.error(f"生成图片失败: {str(e)}")
                    error_text = f"\n\n图片生成失败 ❌ - {str(e)}"
                    error_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {"content": error_text}}],
                        "finish_reason": None
                    }
                    yield f"data: {json.dumps(error_payload)}\n\n"
                
                completion_payload = {
                    "id": unique_id,
                    "object": "chat.completion.chunk",
                    "created": current_timestamp,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": f"\n\n图片处理完成。"}}],
                    "finish_reason": None
                }
                yield f"data: {json.dumps(completion_payload)}\n\n"
                yield "data: [DONE]\n\n"
            
            return Response(
                stream_with_context(generate()),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
            )
        
        # 非流式响应
        else:
            try:
                options = {
                    "size": image_size,
                    "n": 1,
                    "num_images": 1
                }
                
                if seed is not None:
                    options["seed"] = seed
                
                logger.info(f"开始生成图片")
                image_urls = call_provider_api(provider, body["model"], prompt, options)
                
                success, image_text, image_url = process_image_response(image_urls, prompt)
                
                if success:
                    escaped_prompt = json.dumps(safe_prompt)[1:-1]
                    response_text = f"\n{{\n \"prompt\":\"{escaped_prompt}\",\n \"image_size\": \"{image_size}\",\n \"count\": {final_count}\n}}\n\n图片生成完成 ✅\n\n![image|{safe_prompt}]({image_text})"
                    
                    return jsonify({
                        "id": int(time.time() * 1000),
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body["model"],
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": response_text}, "logprobs": None, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": len(body["messages"][-1]["content"]), "completion_tokens": len(response_text), "total_tokens": len(body["messages"][-1]["content"]) + len(response_text)}
                    })
                else:
                    logger.error(f"画图失败：{image_text}")
                    response_text = f"生成图像失败: {image_text}"
                    return jsonify({
                        "id": int(time.time() * 1000),
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body["model"],
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": response_text}, "logprobs": None, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": len(body["messages"][-1]["content"]),


import os
import re
import json
import time
import random
import string
import logging
import secrets
from typing import Dict, List, Any, Optional, Union
import base64
import io

import requests
from flask import Flask, request, Response, jsonify, stream_with_context

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 支持的画图模型列表
SUPPORTED_MODELS = [
    # Flux模型
    "black-forest-labs/FLUX.1-dev",
    "black-forest-labs/FLUX.1",
    
    # Kolors模型
    "Kwai-Kolors/Kolors",
    
    # Stable Diffusion模型
    "stabilityai/stable-diffusion-xl-base-1.0",
    "stabilityai/stable-diffusion-2-1-base",
    "runwayml/stable-diffusion-v1-5",
    
    # Midjourney风格模型
    "prompthero/openjourney",
    
    # 动漫风格模型
    "Linaqruf/anything-v3.0",
    "hakurei/waifu-diffusion",
    
    # 写实风格模型
    "dreamlike-art/dreamlike-photoreal-2.0",
    
    # 其他模型
    "CompVis/stable-diffusion-v1-4",
    "stabilityai/stable-diffusion-2-base"
]

# 类型定义
class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content
    
    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content
        }

class Choice:
    def __init__(self, index: int, message: Message, finish_reason: str):
        self.index = index
        self.message = message
        self.logprobs = None
        self.finish_reason = finish_reason
    
    def to_dict(self):
        return {
            "index": self.index,
            "message": self.message.to_dict(),
            "logprobs": self.logprobs,
            "finish_reason": self.finish_reason
        }

class Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
    
    def to_dict(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens
        }

class ResponsePayload:
    def __init__(self, id: int, object: str, created: int, model: str, choices: List[Choice], usage: Usage):
        self.id = id
        self.object = object
        self.created = created
        self.model = model
        self.choices = choices
        self.usage = usage
    
    def to_dict(self):
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [choice.to_dict() for choice in self.choices],
            "usage": self.usage.to_dict()
        }

# 辅助函数
def get_env(key: str, default: str = "") -> str:
    """获取环境变量，如果不存在则返回默认值"""
    return os.environ.get(key, default)

def get_env_bool(key: str, default: bool = False) -> bool:
    """获取布尔类型环境变量，如果不存在则返回默认值"""
    value = os.environ.get(key, str(default)).lower()
    return value in ("true", "1", "yes", "y", "t")

def get_random_api_key() -> str:
    """从API_KEYS环境变量中随机选择一个API密钥"""
    keys = get_env("API_KEYS", "").split(",")
    if not keys or keys[0] == "":
        raise ValueError("API_KEYS environment variable not set")
    return random.choice(keys)

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
    return "1024x1024"  # 默认分辨率

def moderate_check(text: str) -> bool:
    """检查文本是否包含被禁止的关键词"""
    banned_words = get_env("BANNED_KEYWORDS", "").split(",")
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
    # 检查是否启用短链接服务
    if not get_env_bool("USE_SHORTLINK", False):
        return long_url
    
    if len(long_url) < 30:
        return long_url
    
    base_url = get_env("SHORTLINK_BASE_URL")
    api_key = get_env("SHORTLINK_API_KEY")
    
    if not base_url or not api_key:
        return long_url
    
    slug = generate_random_slug()
    api_url = f"{base_url}/api/link/create"
    
    try:
        response = requests.post(
            api_url,
            json={"url": long_url, "slug": slug},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=5
        )
        
        if response.status_code in (200, 201):
            return f"{base_url}{slug}"
        
        logger.error(f"短链接API错误响应: {response.text}")
    except Exception as e:
        logger.error(f"生成短链接失败: {e}")
    
    return long_url

def upload_to_lsky_pro(image_url: str) -> Optional[str]:
    """上传图片到蓝空图床"""
    # 检查是否启用蓝空图床
    if not get_env_bool("USE_LSKY_PRO", False):
        return None
    
    lsky_url = get_env("LSKY_PRO_URL")
    lsky_token = get_env("LSKY_PRO_TOKEN")
    
    if not lsky_url or not lsky_token:
        logger.error("蓝空图床配置不完整，请检查LSKY_PRO_URL和LSKY_PRO_TOKEN环境变量")
        return None
    
    try:
        # 下载原始图片
        logger.info(f"从 {image_url} 下载图片")
        image_response = requests.get(image_url, timeout=10)
        if image_response.status_code != 200:
            logger.error(f"下载图片失败: {image_response.status_code}")
            return None
        
        # 准备上传到蓝空图床
        upload_url = f"{lsky_url.rstrip('/')}/api/v1/upload"
        
        # 使用multipart/form-data上传
        files = {
            'file': ('image.png', image_response.content, 'image/png')
        }
        
        headers = {
            'Authorization': f'Bearer {lsky_token}'
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
    image_prompt_model = get_env("IMAGE_PROMPT_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    llm_api_url = get_env("LLM_API_URL", "http://localhost:3000/v1/chat/completions")
    
    messages = [
        {
            "role": "system",
            "content": "你是一个技术精湛、善于观察、富有创造力和想象力、擅长使用精准语言描述画面的艺术家。请根据用户的作画请求（可能是一组包含绘画要求的上下文，跳过其中的非绘画内容），扩充为一段具体的画面描述，100 words以内。可以包括画面内容、风格、技法等，使用英文回复."
        },
        {
            "role": "user",
            "content": text
        }
    ]
    
    try:
        response = requests.post(
            llm_api_url,
            json={
                "model": image_prompt_model,
                "messages": messages
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"生成图像提示失败: {e}")
    
    return text

def generate_image_stream(unique_id: int, current_timestamp: int, model: str, prompt: str, 
                         new_url: str, new_request_body: Dict, headers: Dict):
    """生成图像流式响应"""
    # 提示信息
    prompt_payload = {
        "id": unique_id,
        "object": "chat.completion.chunk",
        "created": current_timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": f"```\n{{\n  \"prompt\":\"{prompt}\"\n}}\n```\n"
                }
            }
        ],
        "finish_reason": None
    }
    yield f"data: {json.dumps(prompt_payload)}\n\n"
    
    # 等待一小段时间，确保客户端收到提示信息
    time.sleep(0.5)
    
    # 任务进行中
    task_payload = {
        "id": unique_id,
        "object": "chat.completion.chunk",
        "created": current_timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": "> 生成中"
                }
            }
        ],
        "finish_reason": None
    }
    yield f"data: {json.dumps(task_payload)}\n\n"
    
    # 等待一小段时间，确保客户端收到进行中信息
    time.sleep(0.5)

    # 请求已提交
    submitted_payload = {
        "id": unique_id,
        "object": "chat.completion.chunk",
        "created": current_timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": "\n生成中✅"
                }
            }
        ],
        "finish_reason": None
    }
    yield f"data: {json.dumps(submitted_payload)}\n\n"

    # 等待一小段时间，确保客户端收到提交成功信息
    time.sleep(0.5)
    
    # 调用图像生成API
    try:
        logger.info(f"调用图像生成API: {new_url}")
        logger.info(f"请求头: {headers}")
        logger.info(f"请求体: {new_request_body}")
        
        response = requests.post(
            new_url,
            json=new_request_body,
            headers=headers
        )
        
        logger.info(f"API响应状态码: {response.status_code}")
        logger.info(f"API响应内容: {response.text[:200]}...")
        
        # 确保响应是JSON格式
        try:
            response_body = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"解析API响应失败: {e}, 响应内容: {response.text}")
            response_body = {"message": f"解析API响应失败: {response.text[:100]}..."}
        
        # 检查响应中是否包含图像URL
        if isinstance(response_body, dict) and "images" in response_body and response_body["images"]:
            # 确保images是列表且包含字典元素
            if isinstance(response_body["images"], list) and len(response_body["images"]) > 0:
                image_item = response_body["images"][0]
                if isinstance(image_item, dict) and "url" in image_item:
                    image_url = image_item["url"]
                    logger.info(f"接收到的 imageURL: {image_url}")
                    
                    # 生成短链接
                    short_url = generate_short_url(image_url)
                    
                    # 上传到蓝空图床
                    lsky_url = upload_to_lsky_pro(image_url)
                    
                    # 构建响应文本
                    if lsky_url:
                        task_text = f"✅\n下载链接(链接有时效性，及时下载保存)：{short_url}\n\n蓝空图床链接(永久有效)：{lsky_url}\n\n![{prompt}]({lsky_url})"
                    else:
                        task_text = f"✅\n下载链接(链接有时效性，及时下载保存)：{short_url}\n\n![{prompt}]({short_url})"
                else:
                    logger.error(f"图像项格式错误: {image_item}")
                    task_text = f"❌\n\n\`\`\`\n{{\n  \"message\":\"图像格式错误\"\n}}\n\`\`\`"
            else:
                logger.error(f"图像列表格式错误: {response_body['images']}")
                task_text = f"❌\n\n\`\`\`\n{{\n  \"message\":\"图像列表格式错误\"\n}}\n\`\`\`"
        else:
            error_msg = "未知错误"
            if isinstance(response_body, dict) and "message" in response_body:
                error_msg = str(response_body["message"])
            task_text = f"❌\n\n\`\`\`\n{{\n  \"message\":\"{error_msg}\"\n}}\n\`\`\`"
            logger.error(f"画图失败：{response_body}")
        
        task_payload["choices"][0]["delta"]["content"] = task_text
        yield f"data: {json.dumps(task_payload)}\n\n"
    except Exception as e:
        logger.error(f"生成图像失败: {str(e)}")
        task_text = f"❌\n\n\`\`\`\n{{\n  \"message\":\"服务器错误: {str(e)}\"\n}}\n\`\`\`"
        task_payload["choices"][0]["delta"]["content"] = task_text
        yield f"data: {json.dumps(task_payload)}\n\n"
    
    yield "data: [DONE]\n\n"

def send_response(body: Dict, response_text: str) -> Dict:
    """构建API响应"""
    unique_id = int(time.time() * 1000)
    current_timestamp = int(time.time())
    
    return {
        "id": unique_id,
        "object": "chat.completion",
        "created": current_timestamp,
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text
                },
                "logprobs": None,
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": len(body["messages"][-1]["content"]),
            "completion_tokens": len(response_text),
            "total_tokens": len(body["messages"][-1]["content"]) + len(response_text)
        }
    }

def verify_api_key(request_auth: str) -> bool:
    """验证API密钥"""
    service_api_key = get_env("API_KEY", "")
    
    # 如果未设置API_KEY环境变量，则不进行验证
    if not service_api_key:
        return True
    
    # 检查请求头中的Authorization
    if not request_auth:
        return False
    
    # 提取Bearer token
    parts = request_auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    
    # 验证token
    return parts[1] == service_api_key

# API路由
@app.route("/v1/models", methods=["GET"])
def list_models():
    """列出支持的模型"""
    # 验证API密钥
    if not verify_api_key(request.headers.get("Authorization", "")):
        return jsonify({"error": "Unauthorized: Invalid API key"}), 401
    
    models_data = {
        "object": "list",
        "data": [{"id": model, "object": "model"} for model in SUPPORTED_MODELS]
    }
    return jsonify(models_data)

@app.route("/v1/chat/completions", methods=["POST"])
def handle_request():
    """处理图像生成请求"""
    try:
        # 验证API密钥
        if not verify_api_key(request.headers.get("Authorization", "")):
            return jsonify({"error": "Unauthorized: Invalid API key"}), 401
        
        body = request.json
        
        # 验证请求
        if not body or "model" not in body or "messages" not in body or not body["messages"]:
            return jsonify({"error": "Bad Request: Missing required fields"}), 400
        
        # 检查模型是否下架
        if "janus" in body["model"].lower():
            return jsonify({"error": f"该模型已下架: {body['model']}"}), 410
        
        # 构建完整上下文
        full_context = ""
        for message in body["messages"]:
            if message["role"] != "assistant":
                full_context += message["content"] + "\n\n"
        context = full_context.strip()
        
        # 内容审核
        if moderate_check(context):
            nsfw_response = "Warning: Prohibited Content Detected! 🚫\n\nYour request contains banned keywords. Please check the content and try again.\n\n-----------------------\n\n警告：请求包含被禁止的关键词，请检查后重试！⚠️"
            
            # 流式响应
            if body.get("stream", False):
                def generate():
                    unique_id = int(time.time() * 1000)
                    current_timestamp = int(time.time())
                    
                    # 初始响应
                    initial_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant"},
                                "finish_reason": None,
                                "logprobs": None
                            }
                        ],
                        "system_fingerprint": "fp_default"
                    }
                    yield f"data: {json.dumps(initial_payload)}\n\n"
                    
                    # 分块发送NSFW警告
                    for chunk in nsfw_response:
                        payload = {
                            "id": unique_id,
                            "object": "chat.completion.chunk",
                            "created": current_timestamp,
                            "model": body["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": chunk},
                                    "finish_reason": None,
                                    "logprobs": None
                                }
                            ],
                            "system_fingerprint": "fp_default"
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                    
                    # 结束响应
                    end_payload = {
                        "id": unique_id,
                        "object": "chat.completion.chunk",
                        "created": current_timestamp,
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                                "logprobs": None
                            }
                        ],
                        "system_fingerprint": "fp_default"
                    }
                    yield f"data: {json.dumps(end_payload)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type="text/event-stream")
            
            # 非流式响应
            else:
                response_payload = {
                    "id": int(time.time() * 1000),
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": nsfw_response
                            },
                            "logprobs": None,
                            "finish_reason": "stop"
                        }
                    ],
                    "usage": {
                        "prompt_tokens": len(context),
                        "completion_tokens": len(nsfw_response),
                        "total_tokens": len(context) + len(nsfw_response)
                    }
                }
                return jsonify(response_payload)
        
        # 获取外部API密钥
        try:
            external_api_key = get_random_api_key()
            logger.info(f"使用外部API密钥: {external_api_key[:5]}...")
        except ValueError as e:
            logger.error(f"获取外部API密钥失败: {e}")
            return jsonify({"error": "未配置外部API密钥，请设置API_KEYS环境变量"}), 500
        
        # 生成图像提示
        prompt = generate_image_prompt(external_api_key, context)
        image_size = match_resolution(context)  # 从原始上下文中匹配分辨率，而不是从生成的提示中
        logger.info(f"用户请求的图像尺寸: {image_size}")
        
        # 配置API URL
        api_base_url = get_env("API_BASE_URL", "https://api.siliconflow.cn")
        
        # 根据模型选择合适的API端点
        if body["model"] == "Kwai-Kolors/Kolors":
            new_url = f"{api_base_url}/v1/images/generations"
            new_request_body = {
                "model": body["model"],
                "prompt": prompt,
                "image_size": image_size,
                "batch_size": 1,
                "num_inference_steps": 20,
                "guidance_scale": 7.5
            }
        elif "flux" in body["model"].lower():
            new_url = f"{api_base_url}/v1/image/generations"
            new_request_body = {
                "model": body["model"],
                "prompt": prompt,
                "image_size": image_size,
                "num_inference_steps": 20,
                "prompt_enhancement": True
            }
        else:
            new_url = f"{api_base_url}/v1/{body['model']}/text-to-image"
            new_request_body = {
                "prompt": prompt,
                "image_size": image_size,
                "num_inference_steps": 20
            }
        
        # 设置外部API请求头
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {external_api_key}"
        }
        
        logger.info(f"调用外部API: {new_url}")
        logger.info(f"使用模型: {body['model']}")
        logger.info(f"图像尺寸: {image_size}")
        
        unique_id = int(time.time() * 1000)
        current_timestamp = int(time.time())
        
        # 流式响应
        if body.get("stream", False):
            def generate():
                # 初始响应 - 角色信息
                initial_payload = {
                    "id": unique_id,
                    "object": "chat.completion.chunk",
                    "created": current_timestamp,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                            "logprobs": None
                        }
                    ],
                    "system_fingerprint": "fp_default"
                }
                yield f"data: {json.dumps(initial_payload)}\n\n"
                
                # 确保立即刷新
                time.sleep(0.1)
                
                # 生成图像流
                for chunk in generate_image_stream(unique_id, current_timestamp, body["model"], 
                                                 prompt, new_url, new_request_body, headers):
                    yield chunk
            
            return Response(
                stream_with_context(generate()),
                content_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",  # 禁用Nginx缓冲
                    "Connection": "keep-alive"
                }
            )
        
        # 非流式响应
        else:
            try:
                logger.info(f"发送请求到: {new_url}")
                logger.info(f"请求头: {headers}")
                logger.info(f"请求体: {new_request_body}")
                
                response = requests.post(new_url, json=new_request_body, headers=headers)
                
                logger.info(f"API响应状态码: {response.status_code}")
                logger.info(f"API响应内容: {response.text[:200]}...")
                
                # 确保响应是JSON格式
                try:
                    response_body = response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"解析API响应失败: {e}, 响应内容: {response.text}")
                    return jsonify({"error": f"解析API响应失败: {response.text[:100]}..."}), 500
                
                # 检查响应中是否包含图像URL
                if isinstance(response_body, dict) and "images" in response_body and response_body["images"]:
                    # 确保images是列表且包含字典元素
                    if isinstance(response_body["images"], list) and len(response_body["images"]) > 0:
                        image_item = response_body["images"][0]
                        if isinstance(image_item, dict) and "url" in image_item:
                            image_url = image_item["url"]
                            logger.info(f"接收到的 imageURL: {image_url}")
                            
                            # 生成短链接
                            short_url = generate_short_url(image_url)
                            
                            # 上传到蓝空图床
                            lsky_url = upload_to_lsky_pro(image_url)
                            
                            # 构建响应文本
                            escaped_prompt = json.dumps(prompt)[1:-1]  # 使用json.dumps处理转义
                            
                            if lsky_url:
                                response_text = f"\n{{\n \"prompt\":\"{escaped_prompt}\",\n \"image_size\": \"{image_size}\"\n}}\n\n下载链接(链接有时效性，及时下载保存)：{short_url}\n\n蓝空图床链接(永久有效)：{lsky_url}\n\n![{prompt}]({lsky_url})"
                            else:
                                response_text = f"\n{{\n \"prompt\":\"{escaped_prompt}\",\n \"image_size\": \"{image_size}\"\n}}\n\n下载链接(链接有时效性，及时下载保存)：{short_url}\n\n![{prompt}]({short_url})"
                            
                            return jsonify(send_response(body, response_text))
                        else:
                            logger.error(f"图像项格式错误: {image_item}")
                            return jsonify({"error": "图像格式错误"}), 500
                    else:
                        logger.error(f"图像列表格式错误: {response_body['images']}")
                        return jsonify({"error": "图像列表格式错误"}), 500
                else:
                    error_msg = "未知错误"
                    if isinstance(response_body, dict) and "message" in response_body:
                        error_msg = str(response_body["message"])
                    logger.error(f"画图失败：{response_body}")
                    response_text = f"生成图像失败: {error_msg}"
                    return jsonify(send_response(body, response_text))
            
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500
    
    except Exception as e:
        logger.error(f"Request handling error: {str(e)}")
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """健康检查端点"""
    return "OK", 200

if __name__ == "__main__":
    # 获取端口
    port = int(get_env("PORT", "7860"))
    
    # 获取图像提示模型
    image_prompt_model = get_env("IMAGE_PROMPT_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    
    # 获取API密钥
    service_api_key = get_env("API_KEY", "")
    if service_api_key:
        logger.info("服务API密钥鉴权已启用")
    else:
        logger.warning("服务API密钥鉴权未启用，服务可能被任何人访问")
    
    # 检查外部API密钥
    external_api_keys = get_env("API_KEYS", "").split(",")
    if not external_api_keys or external_api_keys[0] == "":
        logger.error("未配置外部API密钥，请设置API_KEYS环境变量")
    else:
        logger.info(f"已配置 {len(external_api_keys)} 个外部API密钥")
    
    # 检查短链接服务配置
    if get_env_bool("USE_SHORTLINK", False):
        logger.info("短链接服务已启用")
        if not get_env("SHORTLINK_BASE_URL") or not get_env("SHORTLINK_API_KEY"):
            logger.warning("短链接服务配置不完整，请检查SHORTLINK_BASE_URL和SHORTLINK_API_KEY环境变量")
    else:
        logger.info("短链接服务未启用")
    
    # 检查蓝空图床配置
    if get_env_bool("USE_LSKY_PRO", False):
        logger.info("蓝空图床已启用")
        if not get_env("LSKY_PRO_URL") or not get_env("LSKY_PRO_TOKEN"):
            logger.warning("蓝空图床配置不完整，请检查LSKY_PRO_URL和LSKY_PRO_TOKEN环境变量")
    else:
        logger.info("蓝空图床未启用")
    
    logger.info(f"服务配置: 端口={port}, 模型={image_prompt_model}")
    logger.info(f"关键词过滤: {get_env('BANNED_KEYWORDS', '')}")
    logger.info(f"支持的模型数量: {len(SUPPORTED_MODELS)}")
    
    # 启动服务
    app.run(host="0.0.0.0", port=port)

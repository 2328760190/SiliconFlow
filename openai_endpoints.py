import json
import time
import logging
from typing import Dict, List, Any, Optional
from flask import Flask, request, jsonify, Response, stream_with_context
from config_manager import config_manager, ProviderType
from fal_adapter import FalAIAdapter

logger = logging.getLogger(__name__)

def create_openai_app():
    """创建OpenAI兼容的API应用"""
    app = Flask(__name__)
    
    def verify_openai_api_key(request_auth: str) -> bool:
        """验证OpenAI API密钥"""
        system_config = config_manager.get_system_config()
        
        if not system_config.api_key:
            return True
        
        if not request_auth:
            return False
        
        if request_auth.startswith('Bearer '):
            api_key = request_auth[7:]
        elif request_auth.startswith('Key '):
            api_key = request_auth[4:]
        else:
            api_key = request_auth
        
        return api_key == system_config.api_key
    
    def get_provider_for_model(model: str):
        """根据模型名称获取服务商"""
        providers = config_manager.get_all_providers()
        
        for provider in providers:
            if provider.enabled and model in provider.models:
                return provider
        
        return None
    
    def call_image_generation_api(provider, model: str, prompt: str, options: Dict) -> List[str]:
        """调用图像生成API"""
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
            
            import requests
            response = requests.post(url, headers=headers, json=data, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                if "data" in result:
                    return [item["url"] for item in result["data"] if "url" in item]
            
            raise ValueError(f"OpenAI适配器调用失败: {response.text}")
        
        else:
            # 本项目对接类型 - 这里可以调用原有的逻辑
            raise ValueError("本项目对接类型暂不支持OpenAI接口")
    
    @app.route('/v1/models', methods=['GET'])
    def list_models():
        """列出可用模型"""
        if not verify_openai_api_key(request.headers.get("Authorization", "")):
            return jsonify({
                "error": {
                    "message": "Invalid API key provided",
                    "type": "invalid_api_key"
                }
            }), 401
        
        providers = config_manager.get_all_providers()
        models = []
        
        for provider in providers:
            if provider.enabled:
                for model in provider.models:
                    models.append({
                        "id": model,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": f"{provider.provider_type.value}-{provider.name}",
                        "permission": [],
                        "root": model,
                        "parent": None
                    })
        
        return jsonify({"object": "list", "data": models})
    
    @app.route('/v1/images/generations', methods=['POST'])
    def generate_images():
        """OpenAI兼容的图像生成接口"""
        if not verify_openai_api_key(request.headers.get("Authorization", "")):
            return jsonify({
                "error": {
                    "message": "Invalid API key provided",
                    "type": "invalid_api_key"
                }
            }), 401
        
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
        n = data.get('n', 1)
        response_format = data.get('response_format', 'url')
        seed = data.get('seed')
        
        # 查找支持该模型的服务商
        provider = get_provider_for_model(model)
        if not provider:
            return jsonify({
                "error": {
                    "message": f"Model '{model}' not found",
                    "type": "invalid_request_error"
                }
            }), 400
        
        # 准备选项
        options = {
            "size": size,
            "n": n,
            "response_format": response_format,
            "num_images": n
        }
        
        if seed is not None:
            options["seed"] = seed
        
        try:
            # 调用图像生成API
            image_urls = call_image_generation_api(provider, model, prompt, options)
            
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
    
    @app.route('/v1/chat/completions', methods=['POST'])
    def chat_completions():
        """OpenAI兼容的聊天完成接口（支持图像生成）"""
        if not verify_openai_api_key(request.headers.get("Authorization", "")):
            return jsonify({
                "error": {
                    "message": "Invalid API key provided",
                    "type": "invalid_api_key"
                }
            }), 401
        
        data = request.json
        if not data:
            return jsonify({
                "error": {
                    "message": "Missing or invalid request body",
                    "type": "invalid_request_error"
                }
            }), 400
        
        messages = data.get('messages', [])
        model = data.get('model', 'flux-dev')
        stream = data.get('stream', False)
        
        # 获取最后一条用户消息作为提示词
        prompt = ""
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                prompt = msg.get('content', '')
                break
        
        if not prompt:
            error_msg = "I can generate images. Describe what you'd like."
            
            if stream:
                def generate():
                    current_time = int(time.time())
                    
                    # 发送角色
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    
                    # 发送内容
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': error_msg}, 'finish_reason': None}]})}\n\n"
                    
                    # 发送结束
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type='text/event-stream')
            else:
                return jsonify({
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": error_msg},
                        "logprobs": None,
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
                })
        
        # 查找支持该模型的服务商
        provider = get_provider_for_model(model)
        if not provider:
            error_msg = f"Model '{model}' not found"
            
            if stream:
                def generate():
                    current_time = int(time.time())
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': error_msg}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type='text/event-stream')
            else:
                return jsonify({
                    "error": {
                        "message": error_msg,
                        "type": "invalid_request_error"
                    }
                }), 400
        
        try:
            # 调用图像生成
            options = {"size": "1024x1024", "num_images": 1}
            image_urls = call_image_generation_api(provider, model, prompt, options)
            
            # 构建Markdown格式的响应内容
            content = ""
            for i, url in enumerate(image_urls):
                if i > 0:
                    content += "\n\n"
                content += f"![Generated Image {i+1}]({url})"
            
            if stream:
                def generate():
                    current_time = int(time.time())
                    
                    # 发送角色
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    
                    # 发送内容
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
                    
                    # 发送结束
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type='text/event-stream')
            else:
                return jsonify({
                    "id": f"chatcmpl-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "logprobs": None,
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": len(prompt) // 4,
                        "completion_tokens": len(content) // 4,
                        "total_tokens": (len(prompt) + len(content)) // 4
                    }
                })
                
        except Exception as e:
            logger.error(f"图像生成失败: {str(e)}")
            error_msg = f"Unable to generate image: {str(e)}"
            
            if stream:
                def generate():
                    current_time = int(time.time())
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {'content': error_msg}, 'finish_reason': None}]})}\n\n"
                    yield f"data: {json.dumps({'id': f'chatcmpl-{current_time}', 'object': 'chat.completion.chunk', 'created': current_time, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                    yield "data: [DONE]\n\n"
                
                return Response(stream_with_context(generate()), content_type='text/event-stream')
            else:
                return jsonify({
                    "error": {
                        "message": error_msg,
                        "type": "server_error"
                    }
                }), 500
    
    return app

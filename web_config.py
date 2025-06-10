import json
import secrets
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from config_manager import config_manager, ServiceProvider, ProviderType, AIPromptConfig, ImageHostingConfig, ShortLinkConfig, SystemConfig
import logging
import requests

logger = logging.getLogger(__name__)

def create_config_app():
    """创建配置管理Web应用"""
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    
    # 简单的认证装饰器
    def require_auth(f):
        def decorated_function(*args, **kwargs):
            if 'authenticated' not in session:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    
    @app.route('/config/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
            
            # 简单的认证（实际应用中应该使用更安全的方式）
            if username == 'admin' and password == 'admin123':
                session['authenticated'] = True
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'message': '用户名或密码错误'})
        
        return render_template_string(LOGIN_TEMPLATE)
    
    @app.route('/config/logout')
    def logout():
        session.pop('authenticated', None)
        return redirect(url_for('login'))
    
    @app.route('/config')
    @require_auth
    def dashboard():
        return render_template_string(DASHBOARD_TEMPLATE)
    
    @app.route('/config/api/status')
    @require_auth
    def get_status():
        return jsonify(config_manager.get_config_status())
    
    # 服务商管理API
    @app.route('/config/api/providers', methods=['GET'])
    @require_auth
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
    
    @app.route('/config/api/providers', methods=['POST'])
    @require_auth
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
    
    @app.route('/config/api/providers/<provider_id>', methods=['PUT'])
    @require_auth
    def update_provider(provider_id):
        data = request.get_json()
        provider = config_manager.get_provider(provider_id)
        
        if not provider:
            return jsonify({'success': False, 'message': '服务商不存在'})
        
        # 更新字段
        provider.name = data.get('name', provider.name)
        provider.provider_type = ProviderType(data.get('provider_type', provider.provider_type.value))
        provider.base_url = data.get('base_url', provider.base_url).rstrip('/')
        provider.api_keys = data.get('api_keys', '').split(',') if data.get('api_keys') else provider.api_keys
        provider.models = data.get('models', '').split(',') if data.get('models') else provider.models
        provider.enabled = data.get('enabled', provider.enabled)
        provider.updated_at = datetime.now().isoformat()
        
        if config_manager.add_provider(provider):  # add_provider也用于更新
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '更新服务商失败'})
    
    @app.route('/config/api/providers/<provider_id>', methods=['DELETE'])
    @require_auth
    def delete_provider(provider_id):
        if config_manager.delete_provider(provider_id):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': '删除服务商失败'})
    
    @app.route('/config/api/providers/<provider_id>', methods=['GET'])
    @require_auth
    def get_provider(provider_id):
        provider = config_manager.get_provider(provider_id)
        if provider:
            return jsonify({
                'id': provider.id,
                'name': provider.name,
                'provider_type': provider.provider_type.value,
                'base_url': provider.base_url,
                'api_keys': ','.join(provider.api_keys),
                'models': ','.join(provider.models),
                'enabled': provider.enabled
            })
        else:
            return jsonify({'success': False, 'message': '服务商不存在'})
    
    # 获取默认模型API
    @app.route('/config/api/default-models/<provider_type>')
    @require_auth
    def get_default_models(provider_type):
        try:
            ptype = ProviderType(provider_type)
            models = config_manager.get_default_models_for_type(ptype)
            return jsonify({'success': True, 'models': models})
        except ValueError:
            return jsonify({'success': False, 'message': '无效的服务商类型'})
    
    # AI提示词配置API
    @app.route('/config/api/ai-prompt', methods=['GET'])
    @require_auth
    def get_ai_prompt_config():
        config = config_manager.get_ai_prompt_config()
        return jsonify({
            'enabled': config.enabled,
            'model': config.model,
            'api_url': config.api_url,
            'api_key': config.api_key,
            'system_prompt': config.system_prompt
        })
    
    @app.route('/config/api/ai-prompt', methods=['POST'])
    @require_auth
    def set_ai_prompt_config():
        data = request.get_json()
        config = AIPromptConfig(
            enabled=data.get('enabled', True),
            model=data.get('model', ''),
            api_url=data.get('api_url', ''),
            api_key=data.get('api_key', ''),
            system_prompt=data.get('system_prompt', '')
        )
        config_manager.set_ai_prompt_config(config)
        return jsonify({'success': True})
    
    # 图床配置API
    @app.route('/config/api/image-hosting', methods=['GET'])
    @require_auth
    def get_image_hosting_config():
        config = config_manager.get_image_hosting_config()
        return jsonify({
            'enabled': config.enabled,
            'lsky_url': config.lsky_url,
            'username': config.username,
            'password': config.password,
            'token': config.token,
            'auto_get_token': config.auto_get_token
        })
    
    @app.route('/config/api/image-hosting', methods=['POST'])
    @require_auth
    def set_image_hosting_config():
        data = request.get_json()
        
        config = ImageHostingConfig(
            enabled=data.get('enabled', False),
            lsky_url=data.get('lsky_url', ''),
            username=data.get('username', ''),
            password=data.get('password', ''),
            token=data.get('token', ''),
            auto_get_token=data.get('auto_get_token', True)
        )
        
        # 如果启用自动获取Token
        if config.auto_get_token and config.lsky_url and config.username and config.password:
            token = config_manager.auto_get_lsky_token(config.lsky_url, config.username, config.password)
            if token:
                config.token = token
        
        config_manager.set_image_hosting_config(config)
        return jsonify({'success': True, 'token': config.token})
    
    # 短链接配置API
    @app.route('/config/api/shortlink', methods=['GET'])
    @require_auth
    def get_shortlink_config():
        config = config_manager.get_shortlink_config()
        return jsonify({
            'enabled': config.enabled,
            'base_url': config.base_url,
            'api_key': config.api_key
        })
    
    @app.route('/config/api/shortlink', methods=['POST'])
    @require_auth
    def set_shortlink_config():
        data = request.get_json()
        config = ShortLinkConfig(
            enabled=data.get('enabled', False),
            base_url=data.get('base_url', ''),
            api_key=data.get('api_key', '')
        )
        config_manager.set_shortlink_config(config)
        return jsonify({'success': True})
    
    # 系统配置API
    @app.route('/config/api/system', methods=['GET'])
    @require_auth
    def get_system_config():
        config = config_manager.get_system_config()
        return jsonify({
            'port': config.port,
            'max_images_per_request': config.max_images_per_request,
            'banned_keywords': config.banned_keywords,
            'api_key': config.api_key
        })
    
    @app.route('/config/api/system', methods=['POST'])
    @require_auth
    def set_system_config():
        data = request.get_json()
        config = SystemConfig(
            port=data.get('port', 7860),
            max_images_per_request=data.get('max_images_per_request', 4),
            banned_keywords=data.get('banned_keywords', ''),
            api_key=data.get('api_key', '')
        )
        config_manager.set_system_config(config)
        return jsonify({'success': True})
    
    # 导入环境变量API
    @app.route('/config/api/import-env', methods=['POST'])
    @require_auth
    def import_env():
        data = request.get_json()
        keys = data.get('keys', [])
        config_manager.import_from_env(keys)
        return jsonify({'success': True, 'imported_count': len(keys)})
    
    # 模型管理API
    @app.route('/config/api/providers/<provider_id>/models', methods=['POST'])
    @require_auth
    def add_model_to_provider(provider_id):
        """为服务商添加模型"""
        data = request.get_json()
        model_name = data.get('model_name', '').strip()
        
        if not model_name:
            return jsonify({'success': False, 'message': '模型名称不能为空'})
        
        provider = config_manager.get_provider(provider_id)
        if not provider:
            return jsonify({'success': False, 'message': '服务商不存在'})
        
        if model_name in provider.models:
            return jsonify({'success': False, 'message': '模型已存在'})
        
        provider.models.append(model_name)
        provider.updated_at = datetime.now().isoformat()
        
        if config_manager.add_provider(provider):
            return jsonify({'success': True, 'models': provider.models})
        else:
            return jsonify({'success': False, 'message': '添加模型失败'})

    @app.route('/config/api/providers/<provider_id>/models/<model_name>', methods=['DELETE'])
    @require_auth
    def remove_model_from_provider(provider_id, model_name):
        """从服务商中删除模型"""
        provider = config_manager.get_provider(provider_id)
        if not provider:
            return jsonify({'success': False, 'message': '服务商不存在'})
        
        if model_name not in provider.models:
            return jsonify({'success': False, 'message': '模型不存在'})
        
        provider.models.remove(model_name)
        provider.updated_at = datetime.now().isoformat()
        
        if config_manager.add_provider(provider):
            return jsonify({'success': True, 'models': provider.models})
        else:
            return jsonify({'success': False, 'message': '删除模型失败'})

    # 测试API
    @app.route('/config/api/providers/<provider_id>/test', methods=['POST'])
    @require_auth
    def test_provider(provider_id):
        """测试服务商的画图功能"""
        data = request.get_json()
        model_name = data.get('model', '')
        test_prompt = data.get('prompt', 'A beautiful sunset over mountains')
        
        provider = config_manager.get_provider(provider_id)
        if not provider:
            return jsonify({'success': False, 'message': '服务商不存在'})
        
        if not provider.api_keys:
            return jsonify({'success': False, 'message': '服务商未配置API密钥'})
        
        if model_name and model_name not in provider.models:
            return  '服务商未配置API密钥'})
        
        if model_name and model_name not in provider.models:
            return jsonify({'success': False, 'message': '指定的模型不存在'})
        
        # 选择测试模型
        test_model = model_name if model_name else (provider.models[0] if provider.models else 'test-model')
        
        try:
            # 根据服务商类型构建测试请求
            if provider.provider_type == ProviderType.FAL_AI:
                # Fal.ai类型测试
                from fal_adapter import FalAIAdapter
                fal_adapter = FalAIAdapter(provider.api_keys)
                try:
                    image_urls = fal_adapter.call_fal_api(test_prompt, test_model, {"size": "1024x1024"})
                    return jsonify({
                        'success': True,
                        'message': '测试成功',
                        'has_image': len(image_urls) > 0,
                        'image_count': len(image_urls),
                        'response_keys': ['images', 'urls']
                    })
                except Exception as e:
                    return jsonify({'success': False, 'message': f'Fal.ai测试失败: {str(e)}'})
            
            elif provider.provider_type == ProviderType.OPENAI_ADAPTER:
                test_url = f"{provider.base_url.rstrip('/')}/images/generations"
                test_body = {
                    "model": test_model,
                    "prompt": test_prompt,
                    "size": "1024x1024",
                    "n": 1,
                    "response_format": "url"
                }
            else:
                # 本项目对接类型
                if "kolors" in test_model.lower():
                    test_url = f"{provider.base_url.rstrip('/')}/v1/images/generations"
                    test_body = {
                        "model": test_model,
                        "prompt": test_prompt,
                        "image_size": "1024x1024",
                        "batch_size": 1,
                        "num_inference_steps": 20,
                        "guidance_scale": 7.5
                    }
                elif "flux" in test_model.lower():
                    test_url = f"{provider.base_url.rstrip('/')}/v1/image/generations"
                    test_body = {
                        "model": test_model,
                        "prompt": test_prompt,
                        "image_size": "1024x1024",
                        "num_inference_steps": 20,
                        "prompt_enhancement": True
                    }
                else:
                    test_url = f"{provider.base_url.rstrip('/')}/v1/{test_model}/text-to-image"
                    test_body = {
                        "prompt": test_prompt,
                        "image_size": "1024x1024",
                        "num_inference_steps": 20
                    }
            
            # 对于非Fal.ai类型，发送HTTP请求测试
            if provider.provider_type != ProviderType.FAL_AI:
                headers = {
                    "accept": "application/json",
                    "content-type": "application/json",
                    "Authorization": f"Bearer {provider.api_keys[0]}"
                }
                
                logger.info(f"测试服务商 {provider.name}，URL: {test_url}")
                
                # 发送测试请求
                response = requests.post(test_url, json=test_body, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    try:
                        result = response.json()
                        # 简单检查响应是否包含图片数据
                        has_image = False
                        if "images" in result or "data" in result or "url" in result or "b64_json" in result:
                            has_image = True
                        
                        return jsonify({
                            'success': True,
                            'message': '测试成功',
                            'status_code': response.status_code,
                            'has_image': has_image,
                            'response_keys': list(result.keys()) if isinstance(result, dict) else []
                        })
                    except Exception as e:
                        return jsonify({
                            'success': True,
                            'message': f'请求成功但响应解析失败: {str(e)}',
                            'status_code': response.status_code,
                            'response_text': response.text[:200]
                        })
                else:
                    return jsonify({
                        'success': False,
                        'message': f'测试失败，状态码: {response.status_code}',
                        'status_code': response.status_code,
                        'error': response.text[:200]
                    })
        
        except requests.exceptions.Timeout:
            return jsonify({'success': False, 'message': '请求超时，请检查网络连接和API地址'})
        except requests.exceptions.ConnectionError:
            return jsonify({'success': False, 'message': '连接失败，请检查API地址是否正确'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'测试异常: {str(e)}'})
    
    return app

# HTML模板
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>配置管理系统 - 登录</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @keyframes gradient {
            0% {background-position: 0% 50%;}
            50% {background-position: 100% 50%;}
            100% {background-position: 0% 50%;}
        }
        .animated-bg {
            background: linear-gradient(-45deg, #667eea, #764ba2, #f093fb, #f5576c);
            background-size: 400% 400%;
            animation: gradient 15s ease infinite;
        }
    </style>
</head>
<body class="animated-bg min-h-screen flex items-center justify-center">
    <div class="bg-white p-8 rounded-xl shadow-2xl max-w-md w-full backdrop-blur-md bg-opacity-90">
        <div class="text-center mb-8">
            <h1 class="text-3xl font-bold text-gray-800 mb-2">配置管理系统</h1>
            <p class="text-gray-600">图像生成服务配置中心</p>
        </div>
        <form id="loginForm" class="space-y-6">
            <div>
                <label for="username" class="block text-sm font-medium text-gray-700 mb-2">用户名</label>
                <input type="text" id="username" name="username" required 
                       class="w-full px-4 py-3 rounded-lg border border-gray-300 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition duration-200">
            </div>
            <div>
                <label for="password" class="block text-sm font-medium text-gray-700 mb-2">密码</label>
                <input type="password" id="password" name="password" required 
                       class="w-full px-4 py-3 rounded-lg border border-gray-300 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition duration-200">
            </div>
            <button type="submit" 
                    class="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white font-bold py-3 px-4 rounded-lg hover:from-blue-700 hover:to-purple-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition duration-300 ease-in-out transform hover:-translate-y-1 hover:scale-105">
                登录
            </button>
        </form>
        <div class="mt-6 text-center text-sm text-gray-600">
            默认用户名: admin, 密码: admin123
        </div>
    </div>
    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;

            try {
                const response = await fetch('/config/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });

                const result = await response.json();
                if (result.success) {
                    window.location.href = '/config';
                } else {
                    alert('登录失败: ' + result.message);
                }
            } catch (error) {
                alert('登录失败: ' + error.message);
            }
        });
    </script>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>配置管理系统</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <style>
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .gradient-bg {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .provider-type-badge {
            font-size: 0.75rem;
            padding: 0.25rem 0.5rem;
            border-radius: 0.375rem;
            font-weight: 600;
        }
        .provider-native { background-color: #dcfce7; color: #166534; }
        .provider-openai { background-color: #dbeafe; color: #1e40af; }
        .provider-fal { background-color: #fef3c7; color: #92400e; }
    </style>
</head>
<body class="bg-gray-50 min-h-screen" x-data="configApp()">
    <!-- 顶部导航 -->
    <nav class="gradient-bg shadow-lg">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between h-16">
                <div class="flex items-center">
                    <h1 class="text-xl font-bold text-white">图像生成服务配置中心</h1>
                </div>
                <div class="flex items-center space-x-4">
                    <span class="text-white text-sm" x-text="'存储方式: ' + status.config_source"></span>
                    <div class="relative">
                        <button @click="showApiInfo = !showApiInfo" class="text-white hover:text-gray-200 transition duration-200">
                            API接口
                        </button>
                        <div x-show="showApiInfo" @click.away="showApiInfo = false" 
                             class="absolute right-0 mt-2 w-80 bg-white rounded-md shadow-lg py-2 z-50">
                            <div class="px-4 py-2 text-sm text-gray-700">
                                <div class="font-medium mb-2">可用API接口:</div>
                                <div class="space-y-1 text-xs">
                                    <div>• 主服务: http://localhost:7860/v1/chat/completions</div>
                                    <div>• OpenAI兼容: http://localhost:8081/v1/images/generations</div>
                                    <div>• OpenAI聊天: http://localhost:8081/v1/chat/completions</div>
                                    <div>• 模型列表: http://localhost:8081/v1/models</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <a href="/config/logout" class="text-white hover:text-gray-200 transition duration-200">退出</a>
                </div>
            </div>
        </div>
    </nav>

    <div class="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
        <!-- 状态卡片 -->
        <div class="grid grid-cols-1 md:grid-cols-5 gap-6 mb-8">
            <div class="bg-white overflow-hidden shadow rounded-lg">
                <div class="p-5">
                    <div class="flex items-center">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-green-500 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-bold">✓</span>
                            </div>
                        </div>
                        <div class="ml-5 w-0 flex-1">
                            <dl>
                                <dt class="text-sm font-medium text-gray-500 truncate">存储状态</dt>
                                <dd class="text-lg font-medium text-gray-900" x-text="status.config_source"></dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="bg-white overflow-hidden shadow rounded-lg">
                <div class="p-5">
                    <div class="flex items-center">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-bold">#</span>
                            </div>
                        </div>
                        <div class="ml-5 w-0 flex-1">
                            <dl>
                                <dt class="text-sm font-medium text-gray-500 truncate">服务商数量</dt>
                                <dd class="text-lg font-medium text-gray-900" x-text="status.providers_count"></dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="bg-white overflow-hidden shadow rounded-lg">
                <div class="p-5">
                    <div class="flex items-center">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-purple-500 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-bold">R</span>
                            </div>
                        </div>
                        <div class="ml-5 w-0 flex-1">
                            <dl>
                                <dt class="text-sm font-medium text-gray-500 truncate">Redis状态</dt>
                                <dd class="text-lg font-medium text-gray-900" x-text="status.redis_connected ? '已连接' : '未连接'"></dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="bg-white overflow-hidden shadow rounded-lg">
                <div class="p-5">
                    <div class="flex items-center">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-yellow-500 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-bold">S</span>
                            </div>
                        </div>
                        <div class="ml-5 w-0 flex-1">
                            <dl>
                                <dt class="text-sm font-medium text-gray-500 truncate">SQLite状态</dt>
                                <dd class="text-lg font-medium text-gray-900" x-text="status.sqlite_connected ? '已连接' : '未连接'"></dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>

            <div class="bg-white overflow-hidden shadow rounded-lg">
                <div class="p-5">
                    <div class="flex items-center">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center">
                                <span class="text-white text-sm font-bold">API</span>
                            </div>
                        </div>
                        <div class="ml-5 w-0 flex-1">
                            <dl>
                                <dt class="text-sm font-medium text-gray-500 truncate">OpenAI接口</dt>
                                <dd class="text-lg font-medium text-gray-900">已启用</dd>
                            </dl>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 标签页导航 -->
        <div class="bg-white shadow rounded-lg">
            <div class="border-b border-gray-200">
                <nav class="-mb-px flex space-x-8 px-6" aria-label="Tabs">
                    <button @click="activeTab = 'providers'" 
                            :class="activeTab === 'providers' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
                            class="whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition duration-200">
                        服务商管理
                    </button>
                    <button @click="activeTab = 'ai-prompt'" 
                            :class="activeTab === 'ai-prompt' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
                            class="whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition duration-200">
                        AI提示词配置
                    </button>
                    <button @click="activeTab = 'image-hosting'" 
                            :class="activeTab === 'image-hosting' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
                            class="whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition duration-200">
                        图床配置
                    </button>
                    <button @click="activeTab = 'shortlink'" 
                            :class="activeTab === 'shortlink' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
                            class="whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition duration-200">
                        短链接配置
                    </button>
                    <button @click="activeTab = 'system'" 
                            :class="activeTab === 'system' ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
                            class="whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition duration-200">
                        系统配置
                    </button>
                </nav>
            </div>

            <!-- 服务商管理 -->
            <div x-show="activeTab === 'providers'" class="p-6">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-lg font-medium text-gray-900">服务商管理</h2>
                    <button @click="showAddProvider = true" 
                            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                        添加服务商
                    </button>
                </div>

                <!-- 服务商列表 -->
                <div class="overflow-hidden shadow ring-1 ring-black ring-opacity-5 md:rounded-lg">
                    <table class="min-w-full divide-y divide-gray-300">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">名称</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">类型</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">地址</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">密钥数</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">模型数</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">状态</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">操作</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200">
                            <template x-for="provider in providers" :key="provider.id">
                                <tr>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900" x-text="provider.name"></td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                                        <span :class="{
                                            'provider-native': provider.provider_type === 'native',
                                            'provider-openai': provider.provider_type === 'openai_adapter',
                                            'provider-fal': provider.provider_type === 'fal_ai'
                                        }" class="provider-type-badge">
                                            <span x-text="getProviderTypeName(provider.provider_type)"></span>
                                        </span>
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500" x-text="provider.base_url"></td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500" x-text="provider.api_keys_count"></td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500" x-text="provider.models_count"></td>
                                    <td class="px-6 py-4 whitespace-nowrap">
                                        <span :class="provider.enabled ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'" 
                                              class="inline-flex px-2 py-1 text-xs font-semibold rounded-full" 
                                              x-text="provider.enabled ? '启用' : '禁用'"></span>
                                    </td>
                                    <td class="px-6 py-4 whitespace-nowrap text-sm font-medium">
                                        <button @click="editProvider(provider.id)" class="text-blue-600 hover:text-blue-900 mr-3">编辑</button>
                                        <button @click="manageModels(provider.id)" class="text-green-600 hover:text-green-900 mr-3">模型</button>
                                        <button @click="testProvider(provider.id)" class="text-purple-600 hover:text-purple-900 mr-3">测试</button>
                                        <button @click="deleteProvider(provider.id)" class="text-red-600 hover:text-red-900">删除</button>
                                    </td>
                                </tr>
                            </template>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- AI提示词配置 -->
            <div x-show="activeTab === 'ai-prompt'" class="p-6">
                <h2 class="text-lg font-medium text-gray-900 mb-6">AI提示词配置</h2>
                <form @submit.prevent="saveAIPromptConfig" class="space-y-6">
                    <div class="flex items-center">
                        <input type="checkbox" x-model="aiPromptConfig.enabled" 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <label class="ml-2 block text-sm text-gray-900">启用AI提示词增强</label>
                    </div>
                    
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">模型名称</label>
                            <input type="text" x-model="aiPromptConfig.model" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">API地址</label>
                            <input type="url" x-model="aiPromptConfig.api_url" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">API密钥</label>
                        <input type="password" x-model="aiPromptConfig.api_key" 
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">系统提示词</label>
                        <textarea x-model="aiPromptConfig.system_prompt" rows="6" 
                                  class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"></textarea>
                    </div>
                    
                    <button type="submit" 
                            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                        保存配置
                    </button>
                </form>
            </div>

            <!-- 图床配置 -->
            <div x-show="activeTab === 'image-hosting'" class="p-6">
                <h2 class="text-lg font-medium text-gray-900 mb-6">图床配置</h2>
                <form @submit.prevent="saveImageHostingConfig" class="space-y-6">
                    <div class="flex items-center">
                        <input type="checkbox" x-model="imageHostingConfig.enabled" 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <label class="ml-2 block text-sm text-gray-900">启用蓝空图床</label>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">蓝空图床地址</label>
                        <input type="url" x-model="imageHostingConfig.lsky_url" 
                               placeholder="https://your-lsky-instance.com"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">用户名/邮箱</label>
                            <input type="text" x-model="imageHostingConfig.username" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">密码</label>
                            <input type="password" x-model="imageHostingConfig.password" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                    </div>
                    
                    <div class="flex items-center">
                        <input type="checkbox" x-model="imageHostingConfig.auto_get_token" 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <label class="ml-2 block text-sm text-gray-900">自动获取Token</label>
                    </div>
                    
                    <div x-show="imageHostingConfig.token">
                        <label class="block text-sm font-medium text-gray-700 mb-2">当前Token</label>
                        <input type="text" x-model="imageHostingConfig.token" readonly 
                               class="w-full px-3 py-2 border border-gray-300 rounded-md bg-gray-50">
                    </div>
                    
                    <button type="submit" 
                            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                        保存配置
                    </button>
                </form>
            </div>

            <!-- 短链接配置 -->
            <div x-show="activeTab === 'shortlink'" class="p-6">
                <h2 class="text-lg font-medium text-gray-900 mb-6">短链接配置</h2>
                <form @submit.prevent="saveShortlinkConfig" class="space-y-6">
                    <div class="flex items-center">
                        <input type="checkbox" x-model="shortlinkConfig.enabled" 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <label class="ml-2 block text-sm text-gray-900">启用短链接服务</label>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">短链接服务地址</label>
                        <input type="url" x-model="shortlinkConfig.base_url" 
                               placeholder="https://sink-eue.pages.dev"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">API密钥</label>
                        <input type="password" x-model="shortlinkConfig.api_key" 
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <button type="submit" 
                            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                        保存配置
                    </button>
                </form>
            </div>

            <!-- 系统配置 -->
            <div x-show="activeTab === 'system'" class="p-6">
                <h2 class="text-lg font-medium text-gray-900 mb-6">系统配置</h2>
                <form @submit.prevent="saveSystemConfig" class="space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">服务端口</label>
                            <input type="number" x-model="systemConfig.port" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">最大图片数量</label>
                            <input type="number" x-model="systemConfig.max_images_per_request" 
                                   class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        </div>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">服务API密钥</label>
                        <input type="password" x-model="systemConfig.api_key" 
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">禁用关键词 (逗号分隔)</label>
                        <textarea x-model="systemConfig.banned_keywords" rows="3" 
                                  placeholder="porn,nude,naked,sex"
                                  class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"></textarea>
                    </div>
                    
                    <button type="submit" 
                            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                        保存配置
                    </button>
                </form>
            </div>
        </div>
    </div>

    <!-- 添加服务商模态框 -->
    <div x-show="showAddProvider" class="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50" 
         x-transition:enter="ease-out duration-300" x-transition:enter-start="opacity-0" x-transition:enter-end="opacity-100"
         x-transition:leave="ease-in duration-200" x-transition:leave-start="opacity-100" x-transition:leave-end="opacity-0">
        <div class="relative top-20 mx-auto p-5 border w-11/12 md:w-3/4 lg:w-1/2 shadow-lg rounded-md bg-white">
            <div class="mt-3">
                <h3 class="text-lg font-medium text-gray-900 mb-4" x-text="editingProvider ? '编辑服务商' : '添加服务商'"></h3>
                <form @submit.prevent="saveProvider" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">服务商名称</label>
                        <input type="text" x-model="providerForm.name" required 
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">服务商类型</label>
                        <select x-model="providerForm.provider_type" @change="onProviderTypeChange"
                                class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                            <option value="native">本项目对接</option>
                            <option value="openai_adapter">OpenAI适配器</option>
                            <option value="fal_ai">Fal.ai适配器</option>
                        </select>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">API地址</label>
                        <input type="url" x-model="providerForm.base_url" required 
                               placeholder="https://api.example.com 或 https://api.example.com/v1"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                        <p class="mt-1 text-sm text-gray-500">
                            <span x-show="providerForm.provider_type === 'openai_adapter'">OpenAI适配器：如果只填写域名，系统会自动添加/v1</span>
                            <span x-show="providerForm.provider_type === 'fal_ai'">Fal.ai适配器：通常使用 https://queue.fal.run</span>
                            <span x-show="providerForm.provider_type === 'native'">本项目对接：填写完整的API地址</span>
                        </p>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">API密钥 (逗号分隔)</label>
                        <textarea x-model="providerForm.api_keys" rows="3" 
                                  placeholder="sk-key1,sk-key2,sk-key3"
                                  class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"></textarea>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">
                            支持的模型 (逗号分隔)
                            <button type="button" @click="loadDefaultModels" 
                                    class="ml-2 text-xs bg-gray-100 hover:bg-gray-200 px-2 py-1 rounded">
                                加载默认模型
                            </button>
                        </label>
                        <textarea x-model="providerForm.models" rows="4" 
                                  placeholder="模型名称，用逗号分隔"
                                  class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"></textarea>
                        <p class="mt-1 text-sm text-gray-500">留空将自动使用该类型的默认模型</p>
                    </div>
                    
                    <div class="flex items-center">
                        <input type="checkbox" x-model="providerForm.enabled" 
                               class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <label class="ml-2 block text-sm text-gray-900">启用此服务商</label>
                    </div>
                    
                    <div class="flex justify-end space-x-3 pt-4">
                        <button type="button" @click="closeProviderModal" 
                                class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-200 rounded-md hover:bg-gray-300 transition duration-200">
                            取消
                        </button>
                        <button type="submit" 
                                class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 transition duration-200">
                            保存
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- 模型管理模态框 -->
    <div x-show="showModelManager" class="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50" 
         x-transition:enter="ease-out duration-300" x-transition:enter-start="opacity-0" x-transition:enter-end="opacity-100"
         x-transition:leave="ease-in duration-200" x-transition:leave-start="opacity-100" x-transition:leave-end="opacity-0">
        <div class="relative top-20 mx-auto p-5 border w-11/12 md:w-2/3 lg:w-1/2 shadow-lg rounded-md bg-white">
            <div class="mt-3">
                <h3 class="text-lg font-medium text-gray-900 mb-4">模型管理</h3>
            
            <!-- 添加模型 -->
            <div class="mb-6">
                <div class="flex space-x-2">
                    <input type="text" x-model="newModelName" placeholder="输入模型名称" 
                           class="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    <button @click="addModel" 
                            class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition duration-200">
                        添加
                    </button>
                </div>
            </div>
            
            <!-- 模型列表 -->
            <div class="mb-4">
                <h4 class="text-md font-medium text-gray-700 mb-2">当前模型列表</h4>
                <div class="max-h-60 overflow-y-auto">
                    <template x-for="model in currentProviderModels" :key="model">
                        <div class="flex justify-between items-center py-2 px-3 border-b border-gray-200">
                            <span class="text-sm text-gray-900" x-text="model"></span>
                            <button @click="removeModel(model)" 
                                    class="text-red-600 hover:text-red-900 text-sm">删除</button>
                        </div>
                    </template>
                    <div x-show="currentProviderModels.length === 0" class="text-gray-500 text-sm py-4 text-center">
                        暂无模型
                    </div>
                </div>
            </div>
            
            <div class="flex justify-end">
                <button @click="closeModelManager" 
                        class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-200 rounded-md hover:bg-gray-300 transition duration-200">
                    关闭
                </button>
            </div>
        </div>
    </div>
</div>

<!-- 测试模态框 -->
<div x-show="showTestDialog" class="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50" 
     x-transition:enter="ease-out duration-300" x-transition:enter-start="opacity-0" x-transition:enter-end="opacity-100"
     x-transition:leave="ease-in duration-200" x-transition:leave-start="opacity-100" x-transition:leave-end="opacity-0">
    <div class="relative top-20 mx-auto p-5 border w-11/12 md:w-2/3 lg:w-1/2 shadow-lg rounded-md bg-white">
        <div class="mt-3">
            <h3 class="text-lg font-medium text-gray-900 mb-4">测试服务商</h3>
            
            <div class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">测试模型（可选）</label>
                    <input type="text" x-model="testForm.model" 
                           placeholder="留空使用第一个模型"
                           class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">测试提示词</label>
                    <textarea x-model="testForm.prompt" rows="3" 
                              class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"></textarea>
                </div>
                
                <button @click="runTest" 
                        class="w-full bg-purple-600 hover:bg-purple-700 text-white px-4 py-2 rounded-md text-sm font-medium transition duration-200">
                    开始测试
                </button>
                
                <!-- 测试结果 -->
                <div x-show="testResult" class="mt-4">
                    <div x-show="testResult && testResult.loading" class="text-center py-4">
                        <div class="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-purple-600"></div>
                        <span class="ml-2 text-gray-600">测试中...</span>
                    </div>
                    
                    <div x-show="testResult && !testResult.loading" 
                         :class="testResult && testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'" 
                         class="border rounded-md p-4">
                        <h4 class="font-medium mb-2" 
                            :class="testResult && testResult.success ? 'text-green-800' : 'text-red-800'">
                            测试结果
                        </h4>
                        <p class="text-sm" 
                           :class="testResult && testResult.success ? 'text-green-700' : 'text-red-700'" 
                           x-text="testResult ? testResult.message : ''"></p>
                        
                        <div x-show="testResult && testResult.success" class="mt-2 text-sm text-green-600">
                            <p x-show="testResult.status_code">状态码: <span x-text="testResult.status_code"></span></p>
                            <p x-show="testResult.has_image !== undefined">
                                <span x-show="testResult.has_image">✅ 检测到图片数据</span>
                                <span x-show="!testResult.has_image">⚠️ 未检测到图片数据</span>
                            </p>
                            <p x-show="testResult.image_count !== undefined">图片数量: <span x-text="testResult.image_count"></span></p>
                            <p x-show="testResult.response_keys && testResult.response_keys.length > 0">
                                响应字段: <span x-text="testResult.response_keys.join(', ')"></span>
                            </p>
                        </div>
                        
                        <div x-show="testResult && !testResult.success && testResult.status_code" class="mt-2 text-sm text-red-600">
                            <p>状态码: <span x-text="testResult.status_code"></span></p>
                            <p x-show="testResult.error">错误信息: <span x-text="testResult.error"></span></p>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="flex justify-end mt-6">
                <button @click="closeTestDialog" 
                        class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-200 rounded-md hover:bg-gray-300 transition duration-200">
                    关闭
                </button>
            </div>
        </div>
    </div>
</div>

    <script>
        function configApp() {
            return {
                activeTab: 'providers',
                status: {},
                providers: [],
                showAddProvider: false,
                showApiInfo: false,
                editingProvider: null,
                providerForm: {
                    name: '',
                    provider_type: 'native',
                    base_url: '',
                    api_keys: '',
                    models: '',
                    enabled: true
                },
                aiPromptConfig: {
                    enabled: true,
                    model: '',
                    api_url: '',
                    api_key: '',
                    system_prompt: ''
                },
                imageHostingConfig: {
                    enabled: false,
                    lsky_url: '',
                    username: '',
                    password: '',
                    token: '',
                    auto_get_token: true
                },
                shortlinkConfig: {
                    enabled: false,
                    base_url: '',
                    api_key: ''
                },
                systemConfig: {
                    port: 7860,
                    max_images_per_request: 4,
                    banned_keywords: '',
                    api_key: ''
                },
                showModelManager: false,
                showTestDialog: false,
                currentProviderId: null,
                currentProviderModels: [],
                newModelName: '',
                testForm: {
                    model: '',
                    prompt: 'A beautiful sunset over mountains'
                },
                testResult: null,

                async init() {
                    await this.loadStatus();
                    await this.loadProviders();
                    await this.loadAIPromptConfig();
                    await this.loadImageHostingConfig();
                    await this.loadShortlinkConfig();
                    await this.loadSystemConfig();
                },

                getProviderTypeName(type) {
                    const names = {
                        'native': '本项目对接',
                        'openai_adapter': 'OpenAI适配器',
                        'fal_ai': 'Fal.ai适配器'
                    };
                    return names[type] || type;
                },

                async onProviderTypeChange() {
                    // 当服务商类型改变时，可以自动设置一些默认值
                    if (this.providerForm.provider_type === 'fal_ai') {
                        if (!this.providerForm.base_url) {
                            this.providerForm.base_url = 'https://queue.fal.run';
                        }
                    }
                },

                async loadDefaultModels() {
                    try {
                        const response = await fetch(`/config/api/default-models/${this.providerForm.provider_type}`);
                        const result = await response.json();
                        if (result.success) {
                            this.providerForm.models = result.models.join(',');
                        }
                    } catch (error) {
                        console.error('加载默认模型失败:', error);
                    }
                },

                async loadStatus() {
                    try {
                        const response = await fetch('/config/api/status');
                        this.status = await response.json();
                    } catch (error) {
                        console.error('加载状态失败:', error);
                    }
                },

                async loadProviders() {
                    try {
                        const response = await fetch('/config/api/providers');
                        this.providers = await response.json();
                    } catch (error) {
                        console.error('加载服务商失败:', error);
                    }
                },

                async saveProvider() {
                    try {
                        const url = this.editingProvider ? 
                            `/config/api/providers/${this.editingProvider}` : 
                            '/config/api/providers';
                        const method = this.editingProvider ? 'PUT' : 'POST';
                        
                        const response = await fetch(url, {
                            method: method,
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.providerForm)
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            await this.loadProviders();
                            await this.loadStatus();
                            this.closeProviderModal();
                            alert(this.editingProvider ? '服务商更新成功' : '服务商添加成功');
                        } else {
                            alert('操作失败: ' + result.message);
                        }
                    } catch (error) {
                        alert('操作失败: ' + error.message);
                    }
                },

                async editProvider(providerId) {
                    try {
                        const response = await fetch(`/config/api/providers/${providerId}`);
                        const provider = await response.json();
                        
                        this.providerForm = {
                            name: provider.name,
                            provider_type: provider.provider_type,
                            base_url: provider.base_url,
                            api_keys: provider.api_keys,
                            models: provider.models,
                            enabled: provider.enabled
                        };
                        
                        this.editingProvider = providerId;
                        this.showAddProvider = true;
                    } catch (error) {
                        alert('获取服务商信息失败: ' + error.message);
                    }
                },

                async deleteProvider(providerId) {
                    if (!confirm('确定要删除这个服务商吗？')) return;
                    
                    try {
                        const response = await fetch(`/config/api/providers/${providerId}`, {
                            method: 'DELETE'
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            await this.loadProviders();
                            await this.loadStatus();
                            alert('服务商删除成功');
                        } else {
                            alert('删除失败: ' + result.message);
                        }
                    } catch (error) {
                        alert('删除失败: ' + error.message);
                    }
                },

                closeProviderModal() {
                    this.showAddProvider = false;
                    this.editingProvider = null;
                    this.providerForm = {
                        name: '',
                        provider_type: 'native',
                        base_url: '',
                        api_keys: '',
                        models: '',
                        enabled: true
                    };
                },

                async loadAIPromptConfig() {
                    try {
                        const response = await fetch('/config/api/ai-prompt');
                        this.aiPromptConfig = await response.json();
                    } catch (error) {
                        console.error('加载AI提示词配置失败:', error);
                    }
                },

                async saveAIPromptConfig() {
                    try {
                        const response = await fetch('/config/api/ai-prompt', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.aiPromptConfig)
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            alert('AI提示词配置保存成功');
                        } else {
                            alert('保存失败');
                        }
                    } catch (error) {
                        alert('保存失败: ' + error.message);
                    }
                },

                async loadImageHostingConfig() {
                    try {
                        const response = await fetch('/config/api/image-hosting');
                        this.imageHostingConfig = await response.json();
                    } catch (error) {
                        console.error('加载图床配置失败:', error);
                    }
                },

                async saveImageHostingConfig() {
                    try {
                        const response = await fetch('/config/api/image-hosting', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.imageHostingConfig)
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            if (result.token) {
                                this.imageHostingConfig.token = result.token;
                            }
                            alert('图床配置保存成功');
                        } else {
                            alert('保存失败');
                        }
                    } catch (error) {
                        alert('保存失败: ' + error.message);
                    }
                },

                async loadShortlinkConfig() {
                    try {
                        const response = await fetch('/config/api/shortlink');
                        this.shortlinkConfig = await response.json();
                    } catch (error) {
                        console.error('加载短链接配置失败:', error);
                    }
                },

                async saveShortlinkConfig() {
                    try {
                        const response = await fetch('/config/api/shortlink', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.shortlinkConfig)
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            alert('短链接配置保存成功');
                        } else {
                            alert('保存失败');
                        }
                    } catch (error) {
                        alert('保存失败: ' + error.message);
                    }
                },

                async loadSystemConfig() {
                    try {
                        const response = await fetch('/config/api/system');
                        this.systemConfig = await response.json();
                    } catch (error) {
                        console.error('加载系统配置失败:', error);
                    }
                },

                async saveSystemConfig() {
                    try {
                        const response = await fetch('/config/api/system', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.systemConfig)
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            alert('系统配置保存成功');
                        } else {
                            alert('保存失败');
                        }
                    } catch (error) {
                        alert('保存失败: ' + error.message);
                    }
                },

                async manageModels(providerId) {
                    try {
                        const response = await fetch(`/config/api/providers/${providerId}`);
                        const provider = await response.json();
                        
                        this.currentProviderId = providerId;
                        this.currentProviderModels = provider.models ? provider.models.split(',') : [];
                        this.showModelManager = true;
                    } catch (error) {
                        alert('获取模型列表失败: ' + error.message);
                    }
                },

                async addModel() {
                    if (!this.newModelName.trim()) {
                        alert('请输入模型名称');
                        return;
                    }
                    
                    try {
                        const response = await fetch(`/config/api/providers/${this.currentProviderId}/models`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ model_name: this.newModelName.trim() })
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            this.currentProviderModels = result.models;
                            this.newModelName = '';
                            alert('模型添加成功');
                        } else {
                            alert('添加失败: ' + result.message);
                        }
                    } catch (error) {
                        alert('添加失败: ' + error.message);
                    }
                },

                async removeModel(modelName) {
                    if (!confirm(`确定要删除模型 "${modelName}" 吗？`)) return;
                    
                    try {
                        const response = await fetch(`/config/api/providers/${this.currentProviderId}/models/${encodeURIComponent(modelName)}`, {
                            method: 'DELETE'
                        });
                        
                        const result = await response.json();
                        if (result.success) {
                            this.currentProviderModels = result.models;
                            alert('模型删除成功');
                        } else {
                            alert('删除失败: ' + result.message);
                        }
                    } catch (error) {
                        alert('删除失败: ' + error.message);
                    }
                },

                async testProvider(providerId) {
                    try {
                        const response =  await fetch(`/config/api/providers/${providerId}`);
                        const provider = await response.json();
                        
                        this.currentProviderId = providerId;
                        this.testForm.model = provider.models ? provider.models.split(',')[0] : '';
                        this.testResult = null;
                        this.showTestDialog = true;
                    } catch (error) {
                        alert('获取服务商信息失败: ' + error.message);
                    }
                },

                async runTest() {
                    try {
                        this.testResult = { loading: true };
                        
                        const response = await fetch(`/config/api/providers/${this.currentProviderId}/test`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(this.testForm)
                        });
                        
                        this.testResult = await response.json();
                    } catch (error) {
                        this.testResult = { success: false, message: '测试请求失败: ' + error.message };
                    }
                },

                closeModelManager() {
                    this.showModelManager = false;
                    this.currentProviderId = null;
                    this.currentProviderModels = [];
                    this.newModelName = '';
                },

                closeTestDialog() {
                    this.showTestDialog = false;
                    this.currentProviderId = null;
                    this.testForm = { model: '', prompt: 'A beautiful sunset over mountains' };
                    this.testResult = null;
                }
            }
        }
    </script>
</body>
</html>
"""

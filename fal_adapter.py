import requests
import json
import time
import math
import random
import logging
from typing import Dict, List, Any, Optional, Union

logger = logging.getLogger(__name__)

# Fal.ai模型URL配置
FAL_MODEL_URLS = {
    "flux-1.1-ultra": {
        "submit_url": "https://queue.fal.run/fal-ai/flux-pro/v1.1-ultra",
        "status_base_url": "https://queue.fal.run/fal-ai/flux-pro"
    },
    "recraft-v3": {
        "submit_url": "https://queue.fal.run/fal-ai/recraft-v3",
        "status_base_url": "https://queue.fal.run/fal-ai/recraft-v3"
    },
    "flux-1.1-pro": {
        "submit_url": "https://queue.fal.run/fal-ai/flux-pro/v1.1",
        "status_base_url": "https://queue.fal.run/fal-ai/flux-pro"
    },
    "ideogram-v2": {
        "submit_url": "https://queue.fal.run/fal-ai/ideogram/v2",
        "status_base_url": "https://queue.fal.run/fal-ai/ideogram"
    },
    "flux-dev": {
        "submit_url": "https://queue.fal.run/fal-ai/flux/dev",
        "status_base_url": "https://queue.fal.run/fal-ai/flux"
    }
}

class FalAIAdapter:
    def __init__(self, api_keys: List[str], proxies: Optional[Dict] = None):
        self.api_keys = api_keys
        self.proxies = proxies
    
    def get_random_api_key(self) -> str:
        """随机获取API密钥"""
        if not self.api_keys:
            raise ValueError("No Fal.ai API keys available")
        return random.choice(self.api_keys)
    
    def call_fal_api(self, prompt: str, model: str, options: Optional[Dict] = None) -> List[str]:
        """
        调用Fal.ai API生成图像
        
        Args:
            prompt: 图像生成提示词
            model: 模型名称
            options: 附加选项
            
        Returns:
            List[str]: 图像URL列表
        """
        if options is None:
            options = {}
        
        # 准备基本请求参数
        fal_request = {
            "prompt": prompt,
            "num_images": options.get("num_images", 1)
        }
        
        # 添加其他可选参数
        if "seed" in options:
            fal_request["seed"] = options["seed"]
        if "output_format" in options:
            fal_request["output_format"] = options["output_format"]
        
        # 处理图像尺寸
        if "size" in options:
            width, height = map(int, options["size"].split("x"))
            if model in ["flux-1.1-ultra", "ideogram-v2"]:
                # 这些模型使用宽高比
                gcd = math.gcd(width, height)
                fal_request["aspect_ratio"] = f"{width // gcd}:{height // gcd}"
            else:
                # 其他模型使用具体尺寸
                fal_request["image_size"] = {"width": width, "height": height}
        
        # 获取模型URL信息
        model_config = FAL_MODEL_URLS.get(model, FAL_MODEL_URLS["flux-dev"])
        fal_submit_url = model_config["submit_url"]
        fal_status_base_url = model_config["status_base_url"]
        
        logger.info(f"使用Fal.ai模型: {model}, 提交URL: {fal_submit_url}")
        logger.info(f"请求数据: {json.dumps(fal_request)}")
        
        # 重试逻辑
        max_retries = 3
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                # 获取API密钥
                fal_api_key = self.get_random_api_key()
                headers = {
                    "Authorization": f"Key {fal_api_key}",
                    "Content-Type": "application/json"
                }
                
                logger.info(f"尝试 {retry_count+1}/{max_retries+1} - 使用密钥: {fal_api_key[:5]}...{fal_api_key[-5:] if len(fal_api_key) > 10 else ''}")
                
                # 提交请求
                session = requests.Session()
                fal_response = session.post(
                    fal_submit_url,
                    headers=headers,
                    json=fal_request,
                    proxies=self.proxies,
                    timeout=30
                )
                
                if fal_response.status_code != 200:
                    # 处理错误响应
                    try:
                        error_data = fal_response.json()
                        error_message = error_data.get('error', {}).get('message', fal_response.text)
                    except:
                        error_message = fal_response.text
                    
                    logger.error(f"Fal.ai API错误: {fal_response.status_code}, {error_message}")
                    
                    # 处理认证错误
                    if fal_response.status_code in (401, 403):
                        if retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"API密钥认证失败，重试 ({retry_count}/{max_retries})")
                            time.sleep(2 ** retry_count)
                            continue
                        else:
                            raise ValueError(f"Fal.ai认证失败: {error_message}")
                    
                    # 处理其他错误
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.info(f"Fal.ai API错误，重试 ({retry_count}/{max_retries})")
                        time.sleep(2 ** retry_count)
                        continue
                    
                    raise ValueError(f"Fal.ai API错误: {error_message}")
                
                # 解析响应获取请求ID
                fal_data = fal_response.json()
                request_id = fal_data.get("request_id")
                if not request_id:
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.info(f"未获取request_id，重试 ({retry_count}/{max_retries})")
                        time.sleep(2 ** retry_count)
                        continue
                    raise ValueError("Fal.ai响应中缺少request_id")
                
                logger.info(f"获取到request_id: {request_id}")
                
                # 轮询获取结果
                image_urls = self._poll_for_result(request_id, fal_status_base_url, headers)
                
                if image_urls:
                    return image_urls
                elif retry_count < max_retries:
                    retry_count += 1
                    logger.info(f"未获取到图片URL，重试 ({retry_count}/{max_retries})")
                    time.sleep(2 ** retry_count)
                    continue
                else:
                    raise ValueError("未获取到图片URL")
                    
            except Exception as e:
                if retry_count < max_retries:
                    retry_count += 1
                    logger.error(f"发生异常，重试 ({retry_count}/{max_retries}): {str(e)}")
                    time.sleep(2 ** retry_count)
                    continue
                raise ValueError(f"调用Fal.ai API失败: {str(e)}")
        
        raise ValueError("Fal.ai API调用失败，已达到最大重试次数")
    
    def _poll_for_result(self, request_id: str, status_base_url: str, headers: Dict) -> List[str]:
        """轮询获取生成结果"""
        max_polling_attempts = 60
        image_urls = []
        
        for attempt in range(max_polling_attempts):
            logger.info(f"轮询尝试 {attempt+1}/{max_polling_attempts}")
            
            try:
                # 构建状态和结果URL
                status_url = f"{status_base_url}/requests/{request_id}/status"
                result_url = f"{status_base_url}/requests/{request_id}"
                
                # 检查状态
                status_session = requests.Session()
                status_response = status_session.get(
                    status_url,
                    headers=headers,
                    proxies=self.proxies,
                    timeout=30
                )
                
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    status = status_data.get("status")
                    
                    # 处理失败状态
                    if status == "FAILED":
                        raise ValueError("图像生成失败")
                    
                    # 处理完成状态
                    if status == "COMPLETED":
                        logger.info(f"从以下URL获取结果: {result_url}")
                        
                        # 获取结果
                        result_session = requests.Session()
                        result_response = result_session.get(
                            result_url,
                            headers=headers,
                            proxies=self.proxies,
                            timeout=30
                        )
                        
                        if result_response.status_code == 200:
                            result_data = result_response.json()
                            
                            # 提取图片URL
                            if "images" in result_data:
                                images = result_data.get("images", [])
                                for img in images:
                                    if isinstance(img, dict) and "url" in img:
                                        image_urls.append(img.get("url"))
                                        logger.info(f"找到图片URL: {img.get('url')}")
                            
                            if image_urls:
                                return image_urls
                
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"轮询过程中发生错误: {str(e)}")
                time.sleep(2)
        
        return image_urls

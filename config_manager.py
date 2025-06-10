import os
import json
import sqlite3
import redis
import logging
import hashlib
import secrets
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timedelta
import requests
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)

class ProviderType(Enum):
    NATIVE = "native"  # 本项目对接
    OPENAI_ADAPTER = "openai_adapter"  # OpenAI适配器
    FAL_AI = "fal_ai"  # Fal.ai适配器

# 默认模型配置
DEFAULT_MODELS = {
    ProviderType.NATIVE: [
        "black-forest-labs/FLUX.1-dev",
        "black-forest-labs/FLUX.1",
        "Kwai-Kolors/Kolors",
        "stabilityai/stable-diffusion-xl-base-1.0",
        "stabilityai/stable-diffusion-2-1-base",
        "runwayml/stable-diffusion-v1-5",
        "prompthero/openjourney",
        "Linaqruf/anything-v3.0",
        "hakurei/waifu-diffusion",
        "dreamlike-art/dreamlike-photoreal-2.0",
        "CompVis/stable-diffusion-v1-4",
        "stabilityai/stable-diffusion-2-base"
    ],
    ProviderType.OPENAI_ADAPTER: [
        "dall-e-3",
        "dall-e-2",
        "gpt-4-vision-preview",
        "stable-diffusion-xl-base-1.0",
        "midjourney-v6"
    ],
    ProviderType.FAL_AI: [
        "flux-1.1-ultra",
        "recraft-v3", 
        "flux-1.1-pro",
        "ideogram-v2",
        "flux-dev"
    ]
}

@dataclass
class ServiceProvider:
    id: str
    name: str
    provider_type: ProviderType
    base_url: str
    api_keys: List[str]
    models: List[str]
    enabled: bool = True
    created_at: str = None
    updated_at: str = None

@dataclass
class AIPromptConfig:
    enabled: bool = True
    model: str = "Qwen/Qwen3-8B"
    api_url: str = "http://localhost:3000/v1/chat/completions"
    api_key: str = ""
    system_prompt: str = "你是一个技术精湛、善于观察、富有创造力和想象力、擅长使用精准语言描述画面的艺术家。请根据用户的作画请求（可能是一组包含绘画要求的上下文，跳过其中的非绘画内容），扩充为一段具体的画面描述，100 words以内。可以包括画面内容、风格、技法等，使用英文回复."

@dataclass
class ImageHostingConfig:
    enabled: bool = False
    lsky_url: str = ""
    username: str = ""
    password: str = ""
    token: str = ""
    auto_get_token: bool = True

@dataclass
class ShortLinkConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""

@dataclass
class SystemConfig:
    port: int = 7860
    max_images_per_request: int = 4
    banned_keywords: str = ""
    api_key: str = ""  # 服务鉴权密钥

@dataclass
class UserKey:
    id: str
    name: str
    key: str
    level: str  # "admin" 或 "user"
    enabled: bool = True
    created_at: str = None
    updated_at: str = None
    last_used: str = None
    usage_count: int = 0

@dataclass
class AdminConfig:
    username: str = "admin"
    password: str = "admin123"
    updated_at: str = None

@dataclass
class EndpointPermission:
    endpoint: str
    required_level: str  # "guest", "user", "admin"
    enabled: bool = True

class ConfigManager:
    def __init__(self):
        self.redis_client = None
        self.sqlite_conn = None
        self.config_source = "env"  # env, sqlite, redis
        self.redis_prefix = "image_gen_service:"  # Redis键前缀
        self._init_storage()
    
    def _init_storage(self):
        """初始化存储后端"""
        # 尝试连接Redis
        redis_url = os.getenv("REDIS")
        if redis_url:
            try:
                self.redis_client = redis.from_url(redis_url, decode_responses=True)
                self.redis_client.ping()
                self.config_source = "redis"
                logger.info("使用Redis作为配置存储")
                return
            except Exception as e:
                logger.warning(f"Redis连接失败，将使用SQLite: {e}")
        
        # 使用SQLite作为备用
        config_dir = "/app/config"
        os.makedirs(config_dir, exist_ok=True)
        db_path = os.path.join(config_dir, "config.db")
        
        self.sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)
        self.config_source = "sqlite"
        self._init_sqlite_tables()
        logger.info("使用SQLite作为配置存储")
    
    def _init_sqlite_tables(self):
        """初始化SQLite表结构"""
        cursor = self.sqlite_conn.cursor()
        
        # 服务商表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_keys TEXT NOT NULL,
                models TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 用户Key表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key TEXT UNIQUE NOT NULL,
                level TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used TEXT,
                usage_count INTEGER DEFAULT 0
            )
        """)
        
        self.sqlite_conn.commit()
    
    def _get_from_storage(self, key: str) -> Optional[str]:
        """从存储中获取配置"""
        if self.config_source == "redis" and self.redis_client:
            try:
                return self.redis_client.get(f"{self.redis_prefix}config:{key}")
            except Exception as e:
                logger.error(f"Redis读取失败: {e}")
                return None
        
        elif self.config_source == "sqlite" and self.sqlite_conn:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("SELECT value FROM configs WHERE key = ?", (key,))
            result = cursor.fetchone()
            return result[0] if result else None
        
        return None
    
    def _set_to_storage(self, key: str, value: str):
        """保存配置到存储"""
        if self.config_source == "redis" and self.redis_client:
            try:
                self.redis_client.set(f"{self.redis_prefix}config:{key}", value)
            except Exception as e:
                logger.error(f"Redis写入失败: {e}")
        
        elif self.config_source == "sqlite" and self.sqlite_conn:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO configs (key, value, updated_at) 
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
            self.sqlite_conn.commit()

    
    def _delete_from_storage(self, key: str):
        """从存储中删除配置"""
        if self.config_source == "redis" and self.redis_client:
            try:
                self.redis_client.delete(f"{self.redis_prefix}config:{key}")
            except Exception as e:
                logger.error(f"Redis删除失败: {e}")
        
        elif self.config_source == "sqlite" and self.sqlite_conn:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("DELETE FROM configs WHERE key = ?", (key,))
            self.sqlite_conn.commit()
    
    def get_env_with_fallback(self, key: str, default: str = "") -> str:
        """获取配置值，优先级：Redis/SQLite > 环境变量 > 默认值"""
        # 首先尝试从存储中获取
        stored_value = self._get_from_storage(key)
        if stored_value is not None:
            return stored_value
        
        # 然后从环境变量获取
        env_value = os.getenv(key)
        if env_value is not None:
            return env_value
        
        return default
    
    def set_config(self, key: str, value: str):
        """设置配置值"""
        self._set_to_storage(key, value)
    
    def delete_config(self, key: str):
        """删除配置值"""
        self._delete_from_storage(key)
    
    def import_from_env(self, keys: List[str]):
        """从环境变量导入配置到存储"""
        for key in keys:
            env_value = os.getenv(key)
            if env_value:
                self.set_config(key, env_value)
                logger.info(f"从环境变量导入配置: {key}")
    
    def get_default_models_for_type(self, provider_type: ProviderType) -> List[str]:
        """获取指定服务商类型的默认模型列表"""
        return DEFAULT_MODELS.get(provider_type, [])
    
    # 服务商管理
    def add_provider(self, provider: ServiceProvider) -> bool:
        """添加服务商"""
        try:
            provider.created_at = datetime.now().isoformat()
            provider.updated_at = provider.created_at
        
            if self.config_source == "redis" and self.redis_client:
                self.redis_client.set(f"{self.redis_prefix}provider:{provider.id}", json.dumps(asdict(provider)))
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO providers 
                    (id, name, provider_type, base_url, api_keys, models, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    provider.id, provider.name, provider.provider_type.value,
                    provider.base_url, json.dumps(provider.api_keys),
                    json.dumps(provider.models), provider.enabled,
                    provider.created_at, provider.updated_at
                ))
                self.sqlite_conn.commit()
            return True
        except Exception as e:
            logger.error(f"添加服务商失败: {e}")
            return False
    
    def get_provider(self, provider_id: str) -> Optional[ServiceProvider]:
        """获取服务商"""
        try:
            if self.config_source == "redis" and self.redis_client:
                data = self.redis_client.get(f"{self.redis_prefix}provider:{provider_id}")
                if data:
                    provider_dict = json.loads(data)
                    provider_dict['provider_type'] = ProviderType(provider_dict['provider_type'])
                    return ServiceProvider(**provider_dict)
        
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT * FROM providers WHERE id = ?", (provider_id,))
                row = cursor.fetchone()
                if row:
                    return ServiceProvider(
                        id=row[0], name=row[1], provider_type=ProviderType(row[2]),
                        base_url=row[3], api_keys=json.loads(row[4]),
                        models=json.loads(row[5]), enabled=bool(row[6]),
                        created_at=row[7], updated_at=row[8]
                    )
        except Exception as e:
            logger.error(f"获取服务商失败: {e}")
        return None
    
    def get_all_providers(self) -> List[ServiceProvider]:
        """获取所有服务商"""
        providers = []
        try:
            if self.config_source == "redis" and self.redis_client:
                keys = self.redis_client.keys(f"{self.redis_prefix}provider:*")
                for key in keys:
                    data = self.redis_client.get(key)
                    if data:
                        provider_dict = json.loads(data)
                        provider_dict['provider_type'] = ProviderType(provider_dict['provider_type'])
                        providers.append(ServiceProvider(**provider_dict))
        
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT * FROM providers ORDER BY created_at DESC")
                for row in cursor.fetchall():
                    providers.append(ServiceProvider(
                        id=row[0], name=row[1], provider_type=ProviderType(row[2]),
                        base_url=row[3], api_keys=json.loads(row[4]),
                        models=json.loads(row[5]), enabled=bool(row[6]),
                        created_at=row[7], updated_at=row[8]
                    ))
        except Exception as e:
            logger.error(f"获取服务商列表失败: {e}")
        return providers
    
    def delete_provider(self, provider_id: str) -> bool:
        """删除服务商"""
        try:
            if self.config_source == "redis" and self.redis_client:
                self.redis_client.delete(f"{self.redis_prefix}provider:{provider_id}")
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
                self.sqlite_conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除服务商失败: {e}")
            return False
    
    # AI提示词配置
    def get_ai_prompt_config(self) -> AIPromptConfig:
        """获取AI提示词配置"""
        try:
            config_str = self._get_from_storage("ai_prompt_config")
            if config_str:
                config_dict = json.loads(config_str)
                return AIPromptConfig(**config_dict)
        except Exception as e:
            logger.error(f"获取AI提示词配置失败: {e}")
        
        # 返回默认配置
        return AIPromptConfig()
    
    def set_ai_prompt_config(self, config: AIPromptConfig):
        """设置AI提示词配置"""
        self._set_to_storage("ai_prompt_config", json.dumps(asdict(config)))
    
    # 图床配置
    def get_image_hosting_config(self) -> ImageHostingConfig:
        """获取图床配置"""
        try:
            config_str = self._get_from_storage("image_hosting_config")
            if config_str:
                config_dict = json.loads(config_str)
                return ImageHostingConfig(**config_dict)
        except Exception as e:
            logger.error(f"获取图床配置失败: {e}")
        
        return ImageHostingConfig()
    
    def set_image_hosting_config(self, config: ImageHostingConfig):
        """设置图床配置"""
        self._set_to_storage("image_hosting_config", json.dumps(asdict(config)))
    
    def auto_get_lsky_token(self, lsky_url: str, username: str, password: str) -> Optional[str]:
        """自动获取蓝空图床Token"""
        try:
            login_url = f"{lsky_url.rstrip('/')}/api/v1/tokens"
            response = requests.post(login_url, json={
                "email": username,
                "password": password
            }, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") and "data" in result:
                    token = result["data"].get("token")
                    if token:
                        logger.info("成功获取蓝空图床Token")
                        return token
            
            logger.error(f"获取蓝空图床Token失败: {response.text}")
        except Exception as e:
            logger.error(f"获取蓝空图床Token异常: {e}")
        
        return None
    
    # 短链接配置
    def get_shortlink_config(self) -> ShortLinkConfig:
        """获取短链接配置"""
        try:
            config_str = self._get_from_storage("shortlink_config")
            if config_str:
                config_dict = json.loads(config_str)
                return ShortLinkConfig(**config_dict)
        except Exception as e:
            logger.error(f"获取短链接配置失败: {e}")
        
        return ShortLinkConfig()
    
    def set_shortlink_config(self, config: ShortLinkConfig):
        """设置短链接配置"""
        self._set_to_storage("shortlink_config", json.dumps(asdict(config)))
    
    # 系统配置
    def get_system_config(self) -> SystemConfig:
        """获取系统配置"""
        try:
            config_str = self._get_from_storage("system_config")
            if config_str:
                config_dict = json.loads(config_str)
                return SystemConfig(**config_dict)
        except Exception as e:
            logger.error(f"获取系统配置失败: {e}")
        
        # 从环境变量获取默认值
        return SystemConfig(
            port=int(os.getenv("PORT", "7860")),
            max_images_per_request=int(os.getenv("MAX_IMAGES_PER_REQUEST", "4")),
            banned_keywords=os.getenv("BANNED_KEYWORDS", ""),
            api_key=os.getenv("API_KEY", "")
        )
    
    def set_system_config(self, config: SystemConfig):
        """设置系统配置"""
        self._set_to_storage("system_config", json.dumps(asdict(config)))
    
    # 管理员配置管理
    def get_admin_config(self) -> AdminConfig:
        """获取管理员配置"""
        try:
            config_str = self._get_from_storage("admin_config")
            if config_str:
                config_dict = json.loads(config_str)
                return AdminConfig(**config_dict)
        except Exception as e:
            logger.error(f"获取管理员配置失败: {e}")
        
        return AdminConfig()
    
    def set_admin_config(self, config: AdminConfig):
        """设置管理员配置"""
        config.updated_at = datetime.now().isoformat()
        self._set_to_storage("admin_config", json.dumps(asdict(config)))
    
    # 用户Key管理
    def add_user_key(self, user_key: UserKey) -> bool:
        """添加用户Key"""
        try:
            user_key.created_at = datetime.now().isoformat()
            user_key.updated_at = user_key.created_at
            
            if self.config_source == "redis" and self.redis_client:
                self.redis_client.set(f"{self.redis_prefix}user_key:{user_key.id}", json.dumps(asdict(user_key)))
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO user_keys 
                    (id, name, key, level, enabled, created_at, updated_at, last_used, usage_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_key.id, user_key.name, user_key.key, user_key.level,
                    user_key.enabled, user_key.created_at, user_key.updated_at,
                    user_key.last_used, user_key.usage_count
                ))
                self.sqlite_conn.commit()
            return True
        except Exception as e:
            logger.error(f"添加用户Key失败: {e}")
            return False
    
    def get_user_key(self, key_id: str) -> Optional[UserKey]:
        """获取用户Key"""
        try:
            if self.config_source == "redis" and self.redis_client:
                data = self.redis_client.get(f"{self.redis_prefix}user_key:{key_id}")
                if data:
                    return UserKey(**json.loads(data))
            
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT * FROM user_keys WHERE id = ?", (key_id,))
                row = cursor.fetchone()
                if row:
                    return UserKey(
                        id=row[0], name=row[1], key=row[2], level=row[3],
                        enabled=bool(row[4]), created_at=row[5], updated_at=row[6],
                        last_used=row[7], usage_count=row[8] or 0
                    )
        except Exception as e:
            logger.error(f"获取用户Key失败: {e}")
        return None
    
    def get_user_key_by_key(self, key: str) -> Optional[UserKey]:
        """根据Key值获取用户Key"""
        try:
            if self.config_source == "redis" and self.redis_client:
                keys = self.redis_client.keys(f"{self.redis_prefix}user_key:*")
                for key_name in keys:
                    data = self.redis_client.get(key_name)
                    if data:
                        user_key = UserKey(**json.loads(data))
                        if user_key.key == key:
                            return user_key
            
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT * FROM user_keys WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return UserKey(
                        id=row[0], name=row[1], key=row[2], level=row[3],
                        enabled=bool(row[4]), created_at=row[5], updated_at=row[6],
                        last_used=row[7], usage_count=row[8] or 0
                    )
        except Exception as e:
            logger.error(f"根据Key获取用户Key失败: {e}")
        return None
    
    def get_all_user_keys(self) -> List[UserKey]:
        """获取所有用户Key"""
        user_keys = []
        try:
            if self.config_source == "redis" and self.redis_client:
                keys = self.redis_client.keys(f"{self.redis_prefix}user_key:*")
                for key in keys:
                    data = self.redis_client.get(key)
                    if data:
                        user_keys.append(UserKey(**json.loads(data)))
            
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("SELECT * FROM user_keys ORDER BY created_at DESC")
                for row in cursor.fetchall():
                    user_keys.append(UserKey(
                        id=row[0], name=row[1], key=row[2], level=row[3],
                        enabled=bool(row[4]), created_at=row[5], updated_at=row[6],
                        last_used=row[7], usage_count=row[8] or 0
                    ))
        except Exception as e:
            logger.error(f"获取用户Key列表失败: {e}")
        return user_keys
    
    def delete_user_key(self, key_id: str) -> bool:
        """删除用户Key"""
        try:
            if self.config_source == "redis" and self.redis_client:
                self.redis_client.delete(f"{self.redis_prefix}user_key:{key_id}")
            elif self.config_source == "sqlite" and self.sqlite_conn:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("DELETE FROM user_keys WHERE id = ?", (key_id,))
                self.sqlite_conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除用户Key失败: {e}")
            return False
    
    def update_user_key_usage(self, key: str):
        """更新用户Key的使用记录"""
        try:
            user_key = self.get_user_key_by_key(key)
            if user_key:
                user_key.last_used = datetime.now().isoformat()
                user_key.usage_count += 1
                user_key.updated_at = datetime.now().isoformat()
                self.add_user_key(user_key)  # 更新记录
        except Exception as e:
            logger.error(f"更新用户Key使用记录失败: {e}")
    
    # 端点权限管理
    def get_endpoint_permissions(self) -> Dict[str, str]:
        """获取端点权限配置"""
        try:
            config_str = self._get_from_storage("endpoint_permissions")
            if config_str:
                return json.loads(config_str)
        except Exception as e:
            logger.error(f"获取端点权限配置失败: {e}")
        
        # 默认权限配置
        return {
            "/v1/models": "guest",
            "/v1/chat/completions": "user",
            "/v1/images/generations": "user",
            "/gen": "user",
            "/admin": "admin",
            "/config": "admin"
        }
    
    def set_endpoint_permissions(self, permissions: Dict[str, str]):
        """设置端点权限配置"""
        self._set_to_storage("endpoint_permissions", json.dumps(permissions))
    
    def get_config_status(self) -> Dict[str, Any]:
        """获取配置状态信息"""
        return {
            "config_source": self.config_source,
            "redis_connected": self.redis_client is not None and self.config_source == "redis",
            "sqlite_connected": self.sqlite_conn is not None and self.config_source == "sqlite",
            "providers_count": len(self.get_all_providers()),
            "storage_info": {
                "redis_url": os.getenv("REDIS", "未配置") if self.config_source == "redis" else "未使用",
                "sqlite_path": "/app/config/config.db" if self.config_source == "sqlite" else "未使用"
            }
        }

# 全局配置管理器实例
config_manager = ConfigManager()

### SiliconFlow (硅基流动) 功能概览

## 详细使用说明见 [使用说明.md](https://github.com/2328760190/SiliconFlow/main/使用说明.md)



## 🌟 核心功能

### 1️⃣ OpenAI API 兼容接口

SiliconFlow 完全兼容 OpenAI 的 API 接口规范，可以无缝替换现有的 OpenAI API 调用。支持标准的 `/v1/chat/completions` 端点，使用与 OpenAI 相同的请求和响应格式。

```shellscript
curl -X POST http://localhost:7860/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-service-api-key" \
  -d '{
    "model": "stabilityai/stable-diffusion-xl-base-1.0",
    "messages": [
      {"role": "user", "content": "画一只可爱的猫咪，9:16比例"}
    ]
  }'
```

### 2️⃣ 多图片生成 (pic:number)

使用 `pic:number` 语法可以一次性生成多张图片，节省时间和操作步骤。

```plaintext
画一只可爱的猫咪，9:16比例 pic:3
```

这将同时生成 3 张不同的猫咪图片，所有图片使用相同的提示词，但由不同的 API 密钥处理，可能会有细微差异。生成的图片数量受限于环境变量 `MAX_IMAGES_PER_REQUEST` 和可用的 API 密钥数量。

### 3️⃣ 提示词智能扩充

自动将简短提示扩充为详细的图像描述，提高生成质量。例如，用户输入"画一只猫"可能会被扩充为更详细的英文描述，包含更多细节和风格信息。

### 4️⃣ 图像比例控制

支持多种方式指定图像比例和分辨率：

- **直接指定分辨率**：`1024x1024`、`576x1024` 等
- **指定宽高比**：`1:1`、`16:9`、`9:16` 等
- **使用关键词**：`square`/`正方形`、`landscape`/`横向`、`portrait`/`竖屏` 等


### 5️⃣ 流式响应

支持流式响应，实时返回生成过程的状态更新：

1. 首先返回提示信息
2. 然后返回"生成中"状态
3. 接着返回"生成中✅"表示请求已成功提交
4. 最后返回生成结果或错误信息


### 6️⃣ 图床集成

支持集成蓝空图床(Lsky Pro)，提供永久有效的图片链接：

- 自动将生成的图片上传到蓝空图床
- 响应中同时返回原始URL和蓝空图床URL
- 解决图片链接过期问题


### 7️⃣ UI 友好的图片标记

生成的图片使用特殊的 Markdown 格式，便于 UI 识别和处理：

```markdown
![imageN|prompt](图片URL)
```

其中 N 是图片序号（从 1 开始），prompt 是生成图片使用的提示词。

## 🔧 支持的模型

### Flux 模型

- black-forest-labs/FLUX.1-dev
- black-forest-labs/FLUX.1


### Kolors 模型

- Kwai-Kolors/Kolors


### Stable Diffusion 模型

- stabilityai/stable-diffusion-xl-base-1.0
- stabilityai/stable-diffusion-2-1-base
- runwayml/stable-diffusion-v1-5
- CompVis/stable-diffusion-v1-4
- stabilityai/stable-diffusion-2-base


### Midjourney 风格模型

- prompthero/openjourney


### 动漫风格模型

- Linaqruf/anything-v3.0
- hakurei/waifu-diffusion


### 写实风格模型

- dreamlike-art/dreamlike-photoreal-2.0


## ⚙️ 环境变量说明

| 环境变量 | 描述 | 默认值 | 说明
|-----|-----|-----|-----
| PORT | 服务端口 | 7860 | 服务监听的端口
| API_KEY | 服务鉴权密钥 | - | 用于验证客户端访问本服务的密钥
| API_KEYS | 外部画图API密钥列表 | - | 用于访问外部图像生成API的密钥列表，逗号分隔
| MAX_IMAGES_PER_REQUEST | 单次请求最大图片数量 | 4 | 限制单次请求最多可以生成的图片数量
| IMAGE_PROMPT_MODEL | 提示词扩充使用的模型 | Qwen/Qwen2.5-7B-Instruct | 用于扩充提示词的大语言模型
| API_BASE_URL | 外部API基础URL | [https://api.siliconflow.cn](https://api.siliconflow.cn) | 外部图像生成API的基础URL
| USE_SHORTLINK | 是否启用短链接服务 | false | 是否将生成的图片URL转换为短链接
| USE_LSKY_PRO | 是否启用蓝空图床 | false | 是否将生成的图片上传到蓝空图床
| BANNED_KEYWORDS | 禁用关键词列表 | porn,nude,naked,sex,xxx,adult | 内容审核过滤的关键词列表，逗号分隔


## 🔍 API 端点

### 1. 聊天完成接口

**端点**: `/v1/chat/completions`**方法**: POST**功能**: 生成图像

### 2. 模型列表接口

**端点**: `/v1/models`**方法**: GET**功能**: 获取支持的模型列表

### 3. 健康检查接口

**端点**: `/health`**方法**: GET**功能**: 检查服务是否正常运行

## 🚀 部署方式

### Docker 部署

```shellscript
docker build -t siliconflow .
docker run -p 7860:7860 --env-file .env siliconflow
```

### HuggingFace 部署

可以直接部署到 HuggingFace Spaces，体验地址: [https://chb2025-imagen.hf.space](https://chb2025-imagen.hf.space)

## 📝 使用示例

### Python 示例

```python
import openai

# 设置API基础URL和密钥
openai.api_base = "http://localhost:7860/v1"
openai.api_key = "your-service-api-key"  # 这是访问SiliconFlow服务的密钥

# 生成多张图片
response = openai.ChatCompletion.create(
    model="stabilityai/stable-diffusion-xl-base-1.0",
    messages=[
        {"role": "user", "content": "画一只可爱的猫咪，9:16比例 pic:3"}
    ],
    stream=True
)

# 处理流式响应
for chunk in response:
    if hasattr(chunk.choices[0].delta, "content"):
        print(chunk.choices[0].delta.content, end="")
```

### JavaScript 示例

```javascript
import OpenAI from 'openai';

const openai = new OpenAI({
  baseURL: 'http://localhost:7860/v1',
  apiKey: 'your-service-api-key',  // 这是访问SiliconFlow服务的密钥
});

// 生成图片 (流式)
async function generateImageStream() {
  const stream = await openai.chat.completions.create({
    model: 'stabilityai/stable-diffusion-xl-base-1.0',
    messages: [
      { role: 'user', content: '画一只可爱的猫咪，9:16比例 pic:2' }
    ],
    stream: true
  });
  
  for await (const chunk of stream) {
    if (chunk.choices[0]?.delta?.content) {
      process.stdout.write(chunk.choices[0].delta.content);
    }
  }
}

generateImageStream();
```

## ⚠️ 重要说明

- **API_KEY**: 用于验证客户端访问本服务的密钥
- **API_KEYS**: 用于访问外部图像生成API的密钥列表，逗号分隔


请勿混淆这两个环境变量，它们有不同的用途和格式。

## 📞 联系方式

如有问题，请联系: #linux.do@xingkongxiangban

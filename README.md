# AGY2API：可独立部署的官方 Gemini 网关

这个版本已从静态归档改为**实际可运行的服务**，使用部署者自己的 **Google Gemini Developer API Key**，同时提供 Gemini 原生文本接口和 OpenAI 兼容文本聊天。保留 [原 agy2api](https://github.com/su-kaka/agy2api) 的来源说明及 **CNC-1.0 非商业许可**，但不是腾讯云现网程序的逐字复制，也不会导入现网数据。

## Docker Compose 部署

需要 Docker Engine 和 Compose v2，以及有权使用的官方 Gemini API Key。

```sh
git clone https://github.com/illusion0715/agy2cli-source.git
cd agy2cli-source
cp .env.example .env
chmod 600 .env
```

编辑本地 `.env`：把 `GEMINI_API_KEY` 换成你的官方 Key；把 `GATEWAY_API_KEY` 换成**另一把**至少 32 字符的随机 Key（可用 `openssl rand -hex 32` 在自己机器生成）。示例占位值不能启用接口。不要把 `.env` 提交或发给别人。

```sh
docker compose up -d --build
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
```

默认仅绑定宿主机 `127.0.0.1:8080`。其他设备访问时由部署者自行接入支持流式响应的 HTTPS 反向代理；本项目不改 DNS、证书、防火墙或任何现有服务。不要直接将未加 TLS 的端口公开。

在另一个 Bash 终端设置客户端用的网关 Key（不要填写上游 Key）：

```sh
read -r -s -p 'Gateway Key: ' GATEWAY_API_KEY; echo
export GATEWAY_API_KEY
curl --fail-with-body http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer $GATEWAY_API_KEY"
```

从返回的列表选一个你的账号可用、支持 `generateContent` 的文本模型，填入 `MODEL`。模型权限与费用由 Google 决定；下面的生成请求**会访问真实官方 API，可能计费**，本项目验收没有执行真实付费请求。

```sh
export MODEL='替换为列表中的文本模型ID'
curl --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"

curl -N --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"stream\":true}"

curl --fail-with-body "http://127.0.0.1:8080/v1beta/models/$MODEL:generateContent" \
  -H "x-goog-api-key: $GATEWAY_API_KEY" -H 'Content-Type: application/json' \
  -d '{"contents":[{"role":"user","parts":[{"text":"你好"}]}]}'
```

客户端配置：Base URL `http://127.0.0.1:8080/v1`，API Key 填 **GATEWAY_API_KEY**，模型使用列表中实际可用的 ID。Gemini 客户端通过 `x-goog-api-key` 头发送的也必须是网关 Key；不支持把 Key 放在 URL 查询参数中。

## 已实现与边界

| 接口 | 行为 |
|---|---|
| `/` | 内置轻量说明页，无需前端构建 |
| `GET /healthz` | 存活检查，不代表上游账户可用 |
| `GET /readyz` | 两把 Key 的本地配置检查；不会请求 Google |
| `GET /v1/models` | 官方可生成模型列表转换为 OpenAI 格式，处理分页 |
| `GET /v1beta/models` | 官方原生模型列表及分页 |
| `POST /v1/chat/completions` | 普通文本消息、system/developer 指令、常用生成参数、JSON 输出模式、SSE、可选流式 usage |
| `POST /v1beta/models/{model}:generateContent` | 原生文本请求；保留官方响应和安全反馈 |
| `POST /v1beta/models/{model}:streamGenerateContent` | 原生 SSE；另有 `/v1/models/...` 兼容别名 |
| `GET /usage` | 网关 Key 保护的 SQLite 日汇总 |

- OpenAI 当前只支持 `n=1` 和文本消息；不支持 tool/function 历史、图片/音频、Responses API 或 Anthropic 接口。收到不支持字段会明确 422，不会默默忽略并伪造成功。
- 不包含旧版面板、账户池、OAuth/session 导入导出、设备身份模拟、签名特殊处理或凭证轮换。Vertex/ADC 未实现。
- 官方拒绝、安全反馈和配额限制不会被绕开。无自动重试、无换 Key 重试；429 原样保留状态，上游认证失败转为脱敏 502。
- 上游固定为 `https://generativelanguage.googleapis.com/v1beta/`，TLS 校验开启、重定向拒绝。**没有可配置的第三方 Base URL**，也不把客户端鉴权头转发给上游。
- SSE 开始后若上游失败，HTTP 状态已无法改写，会发送明确的错误 data 帧；OpenAI 流以 `[DONE]` 结束。不要只以 HTTP 200 判断流成功。

## 数据、更新与迁移

Compose 的 `gateway-data` 卷持久化 `/data/usage.sqlite3`，仅按天、接口类别、状态记录次数、耗时、token 数，保留 90 天。**不保存提示词、回答、模型名、IP、账号、Key、Cookie 或原始错误内容。** 没有需要迁移的上游账户状态；真实 Key 只在部署者环境中。

- 更新：拉取代码后 `docker compose up -d --build`，卷默认保留。
- 停止：`docker compose down` 保留数据；`down -v` 会删除汇总卷，请自行备份并确认后再用。
- 旧生产版迁移：新目录和独立端口并行启动本版本，重新配置合法官方 Key；先用自己批准的请求验收，再由自己决定是否切客户端地址。**不要复制旧数据库、旧登录态或旧凭据目录。** 本仓库没有自动部署或迁移生产的脚本。

## 本地验收（无真实 API 调用）

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -v

docker build --target test -t agy2api-official:test .
docker build --target runtime -t agy2api-official:local .
# Linux Docker 默认 socket 的真实容器 HTTP/重建持久化验收
python -m tests.container_smoke
```

测试只通过 Python 工厂参数注入 `httpx.MockTransport`，请求 URL 仍为固定官方 HTTPS 主机。运行入口 `web.py` 不读取任何测试模式或自定义上游环境变量。测试镜像与生产镜像分开，生产镜像不含测试服务器或测试 Key。实际验收范围见 [VALIDATION.md](VALIDATION.md)。

架构见 [ARCHITECTURE.md](ARCHITECTURE.md)，来源与重写边界见 [PROVENANCE.md](PROVENANCE.md)。历史容器时间线仅是 `docs/DEPLOYMENT_TIMELINE.md` 的附加档案，不是本版本部署步骤。

官方协议参考：[生成与 SSE](https://ai.google.dev/api/generate-content)、[模型列表](https://ai.google.dev/api/models)、[官方 API Key](https://ai.google.dev/gemini-api/docs/api-key)。本项目未复制或替代官方的权限、安全或配额机制。

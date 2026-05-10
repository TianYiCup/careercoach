# apps/api

> **后端 · FastAPI + LangGraph + PostgreSQL + Redis + Qdrant**

## 技术栈

- Python 3.12 + FastAPI 0.115 + Uvicorn 0.32
- LangGraph 0.2 + LangChain 0.3
- PostgreSQL 16 + Redis 7 + Qdrant 1.10
- LLM: DeepSeek-V3（主）+ 通义千问（备）
- ASR: 阿里云实时 ASR + whisper.cpp
- TTS: Microsoft Edge-TTS
- 内容审核: 阿里云内容安全 + 自建词典
- 追踪: Langfuse
- 包管理: uv

## 目录结构（待初始化）

```
apps/api/
  pyproject.toml
  app/
    main.py              # FastAPI entry
    agents/              # LangGraph 节点
      orchestrator.py
      roleplay.py
      coach.py
      judge.py
      reviewer.py
    services/
      auth.py
      moderation.py     # ★ 红线
      vibe.py
      share.py
    wxapp/              # 小程序专属
      auth.py           # wx-login
      share.py
    models/             # Pydantic
    db/                 # SQLAlchemy + alembic
    llm/                # LLMProvider 抽象
      provider.py
      deepseek.py
      qwen.py
    asr/
      provider.py
      aliyun.py
      whisper_local.py
  tests/
  openapi.yaml          # 接口契约（W1D2 锁定）
  alembic.ini
  Dockerfile
```

## Sprint 0 待办

- [ ] D1: FastAPI hello + `/health` + Sentry
- [ ] D1: alembic 第一个 migration（User 表）
- [ ] D2: **OpenAPI Schema v0.1**（重要！前后端合同）
- [ ] D3: LLMProvider 抽象 + DeepSeek + 通义 adapter
- [ ] D3: LangGraph 3 节点图（RolePlay / Coach / Judge）
- [ ] D3: Langfuse 接入
- [ ] D4: 阿里云 ASR + whisper.cpp 真机 spike
- [ ] D5: 内容审核服务 + 200 对抗样本 v0

## 开发命令

```bash
uv sync                                          # 装依赖
uv run uvicorn app.main:app --reload --port 8000 # 开发
uv run pytest --cov=app                          # 测试
uv run ruff check .                              # lint
uv run ruff format .                             # 格式化
uv run alembic upgrade head                      # 跑 migration
```

## 环境变量

详见根目录 [`.env.example`](../../.env.example)

## 关联文档

- [Foundation §3.3 后端栈](../../docs/careercoach-foundation.md)
- [PRD §6 数据模型 + §7 API](../../docs/careercoach-prd-v2.md)

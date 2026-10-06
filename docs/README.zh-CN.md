# 企业入职与流程知识问答助手

一个可在本地复现的 Python CLI RAG 原型。真实模式使用通义 embedding、SQLite 向量索引和 DeepSeek；无需 Dify。支持中文/英文提问、简单追问、引用原文查看和逐轮记录。

## 快速开始

安装 Python 3.11/3.12 和 uv：

```text
git clone https://github.com/wyou0410/enterprise-knowledge-assistant.git
cd enterprise-knowledge-assistant
uv sync --locked --extra test
uv run --locked knowledge-assistant chat
```

默认为 demo / MOCK：两篇原创虚构资料、确定性关键词匹配和预写回答，不调用模型、无需密钥。安装依赖可能需要网络。它验证操作链路，不能证明模型效果。

可依次输入 `入职第一天做什么？`、`/source 1`、`如何申请电脑？`、`我是员工`、`电脑多久送到？`。`/new` 新对话，`/help` 帮助，`/exit` 退出。

## 真实问答

复制 `.env.example` 为本地 `.env`，填写已有 DeepSeek/通义密钥和显式授权开关；不要将密钥发送到 issue。配置账户允许的模型、通义地域兼容接口与实际资料快照 ID，再建立索引。

```text
uv run --locked knowledge-assistant build-index --config configs/native-baseline.yaml --env-file .env
uv run --locked knowledge-assistant chat --mode live --config configs/native-baseline.yaml --env-file .env
```

完整步骤见 [SETUP.md](SETUP.md)。公开仓库不包含真实全文、向量、历史题集、密钥或账号标识；源 URL 白名单不代表资料已获再分发许可。重新抓取当前网页会产生新快照，不是旧实验精确复现。live 失败不会降级为 MOCK。

## 工程验证与结果

```text
uv run --locked --extra test pytest -q
uv run --locked knowledge-assistant evaluate --mode mock --run-id demo-check
```

CI 检查 Windows/Linux、Python3.11/3.12，无收费 API 或留出数据。本版省略人工答案评分，仅记录自动工程错误、耗时与参考文档覆盖；不宣称正确率或事实支持度已验证。

既有真实实验两版各30 dev/35轮，Top-K3→5的参考文档完整覆盖19/25→21/25，工程错误1/30→0/30。这不是答案质量提升证明。20题留出未正式运行，真实资料包未公开，详见 [EVALUATION.md](EVALUATION.md)。

更多内容：[架构](ARCHITECTURE.md)、[资料与许可](DATA_AND_LICENSE.md)、[贡献方式](../CONTRIBUTING.md)、[安全](../SECURITY.md)。代码和原创演示资料采用 MIT；项目为研究/学习原型，不代表 GitLab 官方服务。开发使用了 AI 辅助，见 [项目来源说明](PROVENANCE.md)。

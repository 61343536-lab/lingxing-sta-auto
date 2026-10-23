# 领星 STA 建单自动化

将领星 API 封装为独立 MCP 工具，由 Agent 根据 Skill 中的业务流程逐步调用，接入公司 AI 中台，并为后续云端部署保留接口。

## 当前进度

已实现本月已完成装箱任务的候选 SP 提取：

运行时日期 → 当月 1 日至当天 → 分页查询装箱任务 → 已完成且货件号为空 → 汇总关联 SP，并保留 PL、店铺和已有 STA。

当前实现包含 4 个 MCP 工具，支持 stdio 和 Streamable HTTP。后续 SP 待发货核验、STA 创建及配送配置尚未实现；真实领星联调需配置企业凭据、店铺范围及出口 IP 白名单。

## 项目内容

- [服务说明与接入配置](services/lingxing-mcp/README.md)
- [MCP 服务代码](services/lingxing-mcp/src/lingxing_mcp/)
- [Agent 业务 Skill](services/lingxing-mcp/skills/lingxing-monthly-packing/SKILL.md)
- [产品需求文档](docs/prd/2026-10-23-fba-sta-automation-prd.md)

## 本地启动

需要 Python >= 3.11 和 uv。

```bash
cd services/lingxing-mcp
uv sync
cp .env.example .env
# 编辑 .env，填写企业凭据与授权店铺
uv run ruff check .
uv run mypy src
uv run pytest
uv run python -m lingxing_mcp
```

.env 和实际凭据不提交 Git。

## 来源与验证

本仓库保留服务、测试、Skill 和 PRD。服务已迁移为 Python，并通过静态检查、
自动化测试与 stdio/HTTP MCP 冒烟验证；测试使用合成数据，未执行真实企业查询。

# 领星 STA 建单自动化

将领星 API 封装为独立 MCP 工具，由 Agent 根据 Skill 中的业务流程逐步调用，接入公司 AI 中台，并为后续云端部署保留接口。

## 当前进度

已实现本月已完成装箱任务的候选 SP 提取：

运行时日期 → 当月 1 日至当天 → 分页查询装箱任务 → 已完成且货件号为空 → 汇总关联 SP，并保留 PL、店铺和已有 STA。

当前实现包含 4 个 MCP 工具，支持 stdio 和 Streamable HTTP。后续 SP 待发货核验、STA 创建及配送配置尚未实现；真实领星联调需配置企业凭据、店铺范围及出口 IP 白名单。

## 项目内容

- [服务说明与接入配置](services/lingxing-mcp/README.md)
- [MCP 服务代码](services/lingxing-mcp/src/)
- [Agent 业务 Skill](services/lingxing-mcp/skills/lingxing-monthly-packing/SKILL.md)
- [产品需求文档](docs/prd/2026-10-23-fba-sta-automation-prd.md)

## 本地启动

需要 Node.js >= 22.12 和 pnpm。

```bash
cd services/lingxing-mcp
pnpm install --frozen-lockfile
cp .env.example .env
# 编辑 .env，填写企业凭据与授权店铺
pnpm check
pnpm test
pnpm build
pnpm start
```

.env 和实际凭据不提交 Git。

## 来源与验证

本仓库提取自 2026-10-23 已完成的领星 STA 项目交付，保留服务、测试、Skill 和 PRD。原交付已通过 TypeScript 检查、构建与 14 项测试；测试使用合成数据，未执行真实企业查询。

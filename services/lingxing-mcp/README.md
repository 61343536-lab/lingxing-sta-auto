# 领星装箱任务 MCP：本月候选 SP 提取

实现用户指令“请帮我处理这个月的建单业务”的第一段：

```text
运行时当前日期（默认北京时间）
→ 当月 1 日至当天
→ 分页读取装箱任务
→ 已完成 + 货件号为空 + 关联类型为 SP
→ 按店铺 / SP 汇总，保留全部来源 PL
```

这是独立的 TypeScript / Node.js 服务。业务顺序在项目 Skill 中，Agent 逐步调用工具；
列表工具只调用一页领星 API，不在工具内部隐藏整段业务编排。

## 已交付范围

| MCP 工具 | 对应能力 | 输出 |
| --- | --- | --- |
| `get_current_month_range` | 读取运行时钟和业务时区 | 当月起止日期、日期字段、授权店铺 |
| `list_packing_tasks` | POST `/basicOpen/packingTask/list` | 一页任务摘要、判断原因、原始分页信息 |
| `get_packing_task` | POST `/basicOpen/packingTask/taskDetail` | 用 PL 单号查询详情，核验货件关联与店铺，返回同形摘要 |
| `summarize_sp_candidates` | 确定性本地汇总，无外部 API 调用 | 去重 SP、每张 SP 对应的全部 PL、已有 STA、待核验记录 |

接口列表与详情均按配置的授权店铺限制输出。没有开放创建 STA、修改装箱、
发货、扣库存等写工具。没有实现下一段“查询 SP 是否待发货”。
当前结果是候选，不代表可以直接创建 STA。

## 启动

运行环境：Node.js >= 22.12，pnpm。使用 Node 的 JSON 原始数字解析能力保留领星长 ID。
依赖由 `pnpm-lock.yaml` 固定。命令在此目录执行。

```bash
pnpm install --frozen-lockfile
cp .env.example .env
```

在本机编辑 `.env`，填写以下配置，不将实际值提交 Git：

| 配置 | 说明 |
| --- | --- |
| `LINGXING_APP_ID` | 企业领星 AppID，也是签名所用的 AES 密钥 |
| `LINGXING_APP_SECRET` | 企业领星 AppSecret |
| `LINGXING_ALLOWED_SIDS` | 允许查询输出的店铺 ID，如 `54,55`；为空则拒绝业务查询 |
| `BUSINESS_TIMEZONE` | 默认 `Asia/Shanghai` |
| `PACKING_DATE_FIELD` | 默认 `create`；按完成日期筛选时设为 `done` |
| `MCP_TRANSPORT` | 本地 `stdio`，远程服务 `http` |

领星后台还需配置服务出口 IP 白名单及对应 API 权限。当前未配置或使用企业真实凭据，
测试使用合成数据；没有执行真实月份业务查询。

```bash
pnpm check
pnpm test
pnpm build
pnpm start
```

缺少领星凭据时，服务仍可启动并查询本月日期；调用业务工具会返回
`MISSING_CREDENTIALS` 或 `MISSING_STORE_SCOPE`，不会伪造空结果。

### 本地 stdio 接入

MCP 客户端的示例配置如下；将路径替换为部署目录的绝对路径。
运行 `main.js` 时无需交互式输入。协议输出只写 stdout，诊断写 stderr。

```json
{
  "mcpServers": {
    "lingxing-packing": {
      "command": "node",
      "args": [
        "--env-file=/ABSOLUTE/PATH/lingxing-mcp/.env",
        "/ABSOLUTE/PATH/lingxing-mcp/dist/main.js"
      ]
    }
  }
}
```

### 远程 HTTP 接入

配置：

```dotenv
MCP_TRANSPORT=http
MCP_HOST=127.0.0.1
MCP_PORT=3100
MCP_BEARER_TOKEN=<独立随机令牌，至少32字符>
MCP_ALLOWED_HOSTS=localhost,127.0.0.1,<公司实际MCP域名>
```

在公司网关后部署，通过 HTTPS 暴露 `/mcp`，转发 Authorization 头。
默认拒绝未知 Host 和浏览器 Origin；确需浏览器来源时，在 `MCP_ALLOWED_ORIGINS`
中按完整 Origin 显式配置。`GET /health` 只检查服务可达性，不代表领星已连通。

`mcp.example.json` 为 WorkBuddy 远程接入模板，需替换真实域名并通过客户端凭据配置传入令牌。
将 `skills/lingxing-monthly-packing/SKILL.md` 作为此连接器的业务 Skill 配置到公司平台。
当前未修改任何现有 WorkBuddy 配置、发布连接器或部署云服务。

一个服务实例对应一个领星应用和一个店铺授权集合。共享此 MCP 令牌的调用者拥有相同范围；
当前没有多用户细分授权。单实例按 API 路径串行协调调用和令牌刷新；
多实例或其他应用共享同一 AppID 时，需要另行协调限流和令牌管理。

## 调用流程示例

1. `get_current_month_range({})`。
   在北京时间 2026-10-23 执行时返回 `startDate=2026-10-01`、`endDate=2026-10-23`。
   日期来自真实运行时钟，不写死在代码里。
2. `list_packing_tasks`：

```json
{
  "startDate": "2026-10-01",
  "endDate": "2026-10-23",
  "searchFieldTime": "create",
  "relateSnType": ["2"],
  "offset": 0,
  "length": 100
}
```

3. 每次取返回的 `pagination.nextOffset` 继续，直到 `hasMore=false`。
   日期范围在本次扫描中保持不变。若某一页全部为其他店铺，仍按原始分页继续。
4. 对货件关联不明的 PL 调用 `get_packing_task`，用详情摘要替换列表记录。
5. 将全部任务摘要交给 `summarize_sp_candidates`，完整读取后才设置
   `paginationComplete=true`。

业务结果的关键结构：

```json
{
  "ok": true,
  "complete": true,
  "relatedSpNumbers": ["SP_EXAMPLE"],
  "candidates": [
    {
      "sid": "54",
      "shipmentSn": "SP_EXAMPLE",
      "packingTasks": [
        { "taskSn": "PL_EXAMPLE_A", "ptId": "原始无损ID", "staIds": [] },
        { "taskSn": "PL_EXAMPLE_B", "ptId": "原始无损ID", "staIds": [] }
      ]
    }
  ],
  "needsReview": []
}
```

以上仅展示结构，示例单号不是实际业务结果。

## 筛选及失败行为

- “已完成”检查 `taskStatus=3`，不使用 `packingCon=3` 替代。
- 日期字段与完成状态独立，不能因为进入已完成页而悄悄切换日期类型。
- 同时检查关联货件及商品行货件号；空格 / 空字符串视为空。
- `relateShipment` 缺失、null、结构异常或货件字段类型异常时进入待核验，
  不直接当作空货件。
- 非 SP 来源、未完成或存在货件号的任务排除。
- 已有 STA 但无 FBA 的任务仍输出，并保留 STA 编号；后续应核验接续。
- 相同 PL 重复返回会去重；同一 PL 互相矛盾的快照进入待核验。
- 同一个 SP 的多张 PL 全部保留；同号跨店铺分别保留业务范围。
- 长整型 ID 保留原始十进制字符串，不使用已经丢精度的 JavaScript number。
- 每页工具返回授权范围内的摘要，不输出原始上游消息、带 token 的 URL 或凭据。
- HTTP / 业务失败返回 `isError=true` 和结构化错误，不转换成“没有任务”。
- 工具不自动跨页；Skill 有分页和重试预算，未取完明确报告不完整。
- 汇总是无外部查询的辅助操作，`complete` 依据 Agent 的分页完成声明，
  不构成服务器签发的全量扫描证明。结果仅用于下一步查 SP，不应直接授权写操作。

## 验证与限制

测试覆盖时区及跨月、日期合法性、签名和授权请求、令牌缓存及并发刷新、
大整数 ID、缺失 / 非空货件、分页异常、多 PL 归并、店铺隔离、
HTTP MCP 客户端整段调用及 stdio 启动。

实际企业接口仍需配置凭据、白名单后验证。当前是单次只读扫描，没有后台调度、
持久分页游标或跨会话数据库；Agent 会话丢失时可从第一页重新扫描。
实时分页不是事务快照，后续建单前仍需复查 SP、PL 和已有货件关系。
现有 WorkBuddy 版本的接入兼容性需在公司平台实测。

## 官方依据

核查日期：2026-10-23。

- [领星接入、签名与限流](https://apidoc.lingxing.com/new/guides/new-instructions/)
- [获取令牌](https://apidoc.lingxing.com/new/api-docs/Authorization/GetToken/)
- [刷新令牌](https://apidoc.lingxing.com/new/api-docs/Authorization/RefreshToken/)
- [装箱任务列表](https://apidoc.lingxing.com/new/api-docs/Warehouse/PackingTaskList/)
- [装箱任务详情](https://apidoc.lingxing.com/new/api-docs/Warehouse/PackingTaskDetail/)
- [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)
- [WorkBuddy 连接器](https://open.workbuddy.cn/docs/connector)

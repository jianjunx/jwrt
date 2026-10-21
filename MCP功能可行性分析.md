# 在 Kwrt 精简固件上增加 MCP 能力 —— 可行性分析

> 目标机型：小米 WR30U（mediatek/filogic，MT7981B）
> 诉求：固件内置 MCP（Model Context Protocol）服务，让 AI Agent 直接对接路由器能力
> 日期：2026-10-21 ｜ 基线：jianjunx/jwrt 精简构建（已移除 NAS/iStore）

---

## 1. 结论（TL;DR）

**可行，且成本很低。** 判定依据：

| 维度 | 现状 | 判定 |
| --- | --- | --- |
| 硬件余量 | MT7981B 双核 A53 1.3GHz / 256MB RAM / 128MB NAND（112M 布局），固件 ~45MB | ✅ 充裕 |
| 运行时 | 固件**已内置** ucode + ucode-mod-ubus/uci/uloop/fs/socket 可选 + nginx-ssl + nginx-mod-ubus | ✅ 零新增运行时 |
| 协议适配 | MCP 标准传输是 Streamable HTTP（单端点 JSON-RPC），本质是「HTTP + JSON」 | ✅ 与 ubus JSON-RPC 同构，纯适配层 |
| 先例 | PC 侧桥接已有（mcp-openwrt、router-mcp）；固件内置方向**尚属空白** | ✅ 有参考、有增量价值 |
| 安全 | 暴露面 = 路由器管理面（root 级） | ⚠️ 最大风险，须做多层防护（§7） |

推荐路线：**ucode 轻量 MCP 守护进程（方案 A）**，新增 ~1MB、RAM 占用几 MB，作为自研包进 `op-packages` feed，随 `mediatek_filogic` 一起编译。若只想先体验，**今天就能用**现成的 PC 侧桥接（方案 D，零固件改动，§4）。

---

## 2. 需求理解：「固件上的 MCP」意味着什么

MCP 是 AI Agent 与外部能力之间的标准协议（JSON-RPC 2.0 之上定义 `tools/list`、`tools/call` 等方法）。在固件上实现 MCP，即路由器自己成为一个 **MCP Server**：

```
┌──────────────┐  MCP (Streamable HTTP, LAN)  ┌─────────────────────────┐
│  AI Agent     │ ───────────────────────────▶ │  WR30U 固件              │
│  (WorkBuddy/  │   POST /mcp  (JSON-RPC)      │  ┌───────────────────┐  │
│   Claude 等)  │ ◀─────────────────────────── │  │ mcp-routerd (ucode)│  │
└──────────────┘   tools/call → 结果           │  └────────┬──────────┘  │
                                               │           ▼ ubus/uci    │
                                               │  network / wireless /   │
                                               │  firewall / dhcp / log  │
                                               └─────────────────────────┘
```

对比另一种形态（**不在固件上**）：MCP Server 跑在 PC，通过 SSH/ubus 远程操作路由器。两者差异：

| | 固件内置 MCP（本分析对象） | PC 侧桥接（现成方案） |
| --- | --- | --- |
| 部署 | 刷机即有，任何 Agent 直连 | 每台客户端装一份 |
| 协议 | HTTP，Agent 原生支持 | 多为 stdio，需 Agent 本机启动 |
| 依赖 | 无外部依赖 | 依赖路由器 SSH 可达 + PC 常开 |
| 适合 | 家庭/团队共用路由器能力入口 | 个人临时调试 |

**推荐两者并行：先用 D 验证工具面设计，再落 A 进固件。**

---

## 3. 硬件与资源评估

### 3.1 WR30U 资源账

| 资源 | 数值 | MCP 服务占用（ucode 方案） | 余量判定 |
| --- | --- | --- | --- |
| CPU | MT7981B 2×A53 @1.3GHz | 管理面低频 JSON 处理，<1% | ✅ |
| RAM | 256MB（固件运行后空闲通常 150MB+） | ucode 常驻 ~2–5MB | ✅ |
| Flash | 128MB NAND / 112M 布局，固件 ~45MB | 新增包 ~1MB（含 LuCI 页 <2MB） | ✅ |
| 网络 | 千兆交换，LAN 可达 | MCP 仅监听 LAN/回环 | ✅ |

WR30U **无 USB**——正好与我们移除 NAS 的精简方向一致；MCP 的价值主张从「存储」转向「网络管理」（WiFi、防火墙、DHCP、监控、诊断）。

### 3.2 固件已有的关键组件（来自 wr30u manifest 实测）

| 组件 | 状态 | 对 MCP 的意义 |
| --- | --- | --- |
| `ucode` + `ucode-mod-ubus` / `-uci` / `-uloop` / `-fs` / `-log` | ✅ 已内置 | MCP 服务本体的实现语言与路由器 API 绑定 |
| `ucode-mod-socket` | 包索引有（Depends 仅 libc+ucode，~15KB） | 需补装，用于 TCP/HTTP 监听 |
| `nginx-ssl` + `nginx-mod-luci` + `nginx-mod-ubus` | ✅ 已内置 | TLS 终结 + 反代 `/mcp`；`/ubus` JSON-RPC 即先例 |
| `rpcd` + `rpcd-mod-ucode` | ✅ 已内置 | 插件化扩展管理接口的备选宿主 |
| `libwebsockets-full` / `ttyd` | ✅ 已内置 | 说明固件已有长连接服务，网络栈无压力 |

**结论：不需要引入 Node.js/Python 等重型运行时。** 对比：

| 实现载体 | 新增安装体积 | RAM | 判定 |
| --- | --- | --- | --- |
| **A. ucode 守护进程** | **~0.5–1MB** | **~2–5MB** | ✅ 推荐 |
| B. Node.js（@modelcontextprotocol/sdk） | ~30–40MB（node 运行时） | 40–80MB | ❌ 与 256MB 设备不成比例 |
| C. 静态 Go/Rust 二进制（mcp-go / rmcp） | ~8–15MB | ~10–20MB | 🟡 可行，适合工具面复杂后演进 |
| D. PC 侧桥接（SSH） | 0（固件不动） | 0 | ✅ 过渡方案（§4） |

---

## 4. 现有先例（实测调研）

| 项目 | 形态 | 说明 | 对我们的启示 |
| --- | --- | --- | --- |
| [ramanarupa/mcp-openwrt](https://github.com/ramanarupa/mcp-openwrt) | Node.js，跑在 PC，SSH 进路由器 | 工具面最全：网络/WireGuard/DHCP/系统/文件/服务/脚本 | 工具清单设计的现成参考 |
| [@subashgautam/router-mcp](https://socket.dev/npm/package/@subashgautam/router-mcp) | TypeScript，跑在 PC，SSH + ubus | **默认只读**，写操作 `--allow-write`、执行 `--allow-exec` 显式开启 | 安全分层模型直接照搬（§6.3） |
| Oasis（OpenWrt 论坛 2025-10） | LuCI 内 AI 助手，**OLT 工具层跑在路由器** | 明确「工具由服务端定义控制，AI 只提议」，工具经 ubus 注册执行 | 证明「路由器侧工具层」路线成立，但它走自有协议非 MCP |
| （空白） | **固件内置 MCP Server** | 未发现现成开源实现 | 我们做的是增量创新，无兼容包袱 |

注意：mcp-openwrt README 声称「仅支持 OpenWrt 25.x 的 apk 包管理」——Kwrt 25.12 仍用 opkg，若短期用方案 D 需确认兼容或改用 router-mcp（其 OpenWrt adapter 走 ubus/uci，不挑包管理器）。

---

## 5. MCP 协议适配性分析

### 5.1 传输层

MCP 标准远程传输是 **Streamable HTTP**：单端点（`POST /mcp`）收 JSON-RPC，可选 SSE 流式返回。对路由器实现非常友好：

- 无状态变体（每请求独立 JSON 应答）就是「HTTP POST → JSON」，ucode 几百行可实现；
- 2026-07-28 协议修订进一步简化：移除会话握手、`initialize` 可选化、每请求自带版本与能力（`_meta`）——服务端更薄；
- 现实兼容面：当前主流客户端（Claude Desktop、WorkBuddy 等）仍以 2025-03-26 / 2025-06-18 / 2025-11-25 版为主。**实现经典子集即可全兼容**：`initialize` + `notifications/initialized` + `tools/list` + `tools/call` + `ping`，其余（resources/prompts）留二期。

### 5.2 语义层

MCP 的 `tools/call` 与 ubus 的 `call` 是同构的「方法名 + 参数 → 结构化结果」：

| MCP | ubus/uci 对应 | 示例 |
| --- | --- | --- |
| tool: `network_status` | `ubus call network.interface dump` | WAN/LAN 状态 |
| tool: `wifi_scan` | `ubus call iwinfo scan '{"device":"phy0"}'` | 周边 AP |
| tool: `list_clients` | `ubus call luci-rpc getHostHints` / dhcp leases | 在线设备 |
| tool: `firewall_rules` | `uci show firewall` / nft list | 防火墙规则 |
| tool: `system_info` | `ubus call system info` + `board()` | 负载/内存/温度 |
| tool: `read_log` | `logread`（fs/log 模块） | 系统日志 |
| tool: `restart_service`（写） | `ubus call service restart` | 重启 dnsmasq 等 |

### 5.3 协议演进风险

MCP 规范 18 个月内迭代了 4 个版本（2025-03-26 → 2025-06-18 → 2025-11-25 → 2026-07-28）。风险与对策：

- **对策一**：只实现稳定核心（tools 三件套），不追 resources/prompts/tasks 等仍在变的部分；
- **对策二**：`initialize` 响应里返回保守的 `protocolVersion`（如 `2025-06-18`），对旧新客户端都稳；
- **对策三**：把「协议层」与「工具层」分离（两个 ucode 模块），协议变了只改协议层。

---

## 6. 推荐方案设计（方案 A：ucode `mcp-routerd`）

### 6.1 架构

```
AI Agent ──HTTPS──▶ nginx-ssl (:443)
                     │  location /mcp { proxy_pass http://127.0.0.1:8765; }
                     ▼
              mcp-routerd (ucode + uloop + socket, 监听 127.0.0.1:8765)
                     │  token 校验 → JSON-RPC 分发 → 工具注册表
                     ▼
              ubus / uci / nl80211（ucode-mod-ubus 等，均为现有绑定）
```

设计要点：

1. **复用 nginx-ssl 做 TLS 与入口**——固件已有证书体系（LuCI HTTPS），MCP 端点挂在 `/mcp`，不新开公网端口；也可独立监听 `LAN_IP:8765`（仅 br-lan）。
2. **协议层（~300 行）与工具层（逐个工具注册）分离**，工具层即「能力清单」，可按需增删。
3. **无状态 JSON 应答优先**，长任务（如固件备份）用轮询式 task id（二期）。

### 6.2 工具面建议（分层）

**L0 只读（默认开启）**——安全、可放心让 Agent 用：

| 工具名 | 底层 | 用途 |
| --- | --- | --- |
| `system_info` | ubus system info / board | 型号、负载、内存、运行时长 |
| `network_status` | network.interface dump | WAN/LAN/VPN 状态、IP |
| `wifi_status` / `wifi_scan` | network.wireless / iwinfo | 信道、连接数、周边环境 |
| `list_clients` | luci-rpc getHostHints / dhcp | 在线设备、占用带宽 |
| `firewall_rules` | uci firewall / nft | 规则审计 |
| `dns_query_log` | dnsmasq 租约/日志 | 解析记录 |
| `read_log` | logread | 系统日志检索 |
| `speedtest`（可选） | 外部脚本 | WAN 测速 |

**L1 写操作（默认关闭，uci 开关 + Agent 参数二次确认）**：

| 工具名 | 底层 | 风险 |
| --- | --- | --- |
| `set_wifi`（SSID/密码/信道） | uci wireless + reload | 断网风险 |
| `set_dhcp_lease`（静态绑定） | uci dhcp | 低 |
| `add_firewall_rule` / `remove_firewall_rule` | uci firewall | 高（把自己锁外面） |
| `restart_service`（白名单：network/wireless/dnsmasq） | ubus service | 中 |
| `reboot` | ubus system reboot | 高（需 `confirm: true` 参数） |

**L2 执行（默认关闭，建议长期关闭）**：任意 shell。router-mcp 的 `--allow-exec` 仅调试用；固件内置版建议**干脆不做**——有了 L0/L1，Agent 不需要裸 shell。

### 6.3 安全模型（照搬 router-mcp 的分层 + 加固）

1. **认证**：Bearer token（`/etc/config/mcp` 中 `option token`，首次启动生成随机值）；nginx 层也可加 HTTP Basic 双保险。
2. **绑定**：仅监听 `127.0.0.1:8765`（经 nginx 反代）或 `br-lan` 地址；**绝不监听 WAN**，防火墙默认不放行。
3. **分级**：L0 默认开、L1 由 `uci set mcp.@server[0].allow_write=1` 显式开启、L2 不实现。
4. **审计**：每次 `tools/call` 记 `logread` 可查的审计行（时间、工具、参数摘要、结果码），出问题可溯源。
5. **防提示注入**：文档中明确告诫——Agent 可能被网页/文件里的恶意指令操纵；只读默认 + 写操作二次确认参数（如 `confirm: true`）是最后防线。
6. **升级保留**：token 与开关放 uci，`sysupgrade` 保配置时行为可预期（对照本项目 §12 风险清单的 overlay 残留问题，MCP 包自身无历史残留，因其是新增功能）。

---

## 7. 风险与副作用评估

| # | 风险 | 等级 | 说明与对策 |
| --- | --- | --- | --- |
| R1 | **管理面暴露**：MCP 即 root 级路由器控制面，token 泄露 = 网络被控 | 🔴 高 | §6.3 全套：token + LAN 绑定 + 只读默认 + 审计；文档标注「不要映射公网」 |
| R2 | **提示注入**：恶意网页诱导 Agent 调写工具 | 🔴 高 | L1 默认关 + `confirm` 参数 + 审计；Agent 侧建议人工确认写操作 |
| R3 | Agent 误操作（改 WiFi 密码/防火墙把自己锁外面） | 🟡 中 | L1 工具内置「回滚窗口」（如网络重启失败 60s 自动回退 uci），二期实现 |
| R4 | MCP 规范快速演进，实现过时 | 🟡 中 | §5.3：只做稳定核心、协议层/工具层分离 |
| R5 | ucode 单线程，长任务阻塞 | 🟢 低 | 管理面 QPS 极低；长任务用异步 task id（二期） |
| R6 | 与精简原则冲突（增加体积/攻击面） | 🟢 低 | 增量 ~1MB 且功能正交；做成**可选包**，`.config` 一行可关，不破坏精简基线 |
| R7 | nginx `/mcp` 与 LuCI 同域的 CSRF 面 | 🟢 低 | `/mcp` 只接受 `Authorization: Bearer` 头，拒绝 cookie 认证，天然免疫 CSRF |
| R8 | 升级兼容：Kwrt 上游 rebase 时自研包维护成本 | 🟢 低 | 包独立放 op-packages feed（我们已有删包 commit 的同一仓库），与上游无耦合 |

**不建议做的**：公网直暴露 MCP（哪怕带 token）、L2 裸 shell、把 MCP 接到 iStore/商店类能力（已精简移除，别复活）。

---

## 8. 与现有构建体系的集成点（落地改动预告）

若确认做，改动全部落在我们自己的仓库，不碰上游：

| 文件/位置 | 改动 | 说明 |
| --- | --- | --- |
| `op-packages/mcp-routerd/`（新增） | 包目录：`Makefile` + `files/usr/bin/mcp-routerd`（ucode 脚本）+ `files/etc/init.d/mcp-routerd` + `files/etc/config/mcp` + `files/etc/uci-defaults/99-mcp-token` | ucode 包，Depends: `+ucode +ucode-mod-ubus +ucode-mod-uci +ucode-mod-uloop +ucode-mod-socket` |
| `op-packages/luci-app-mcp/`（新增，二期） | LuCI 页：开关/工具分级/token 管理/审计查看 | 对应 `luci-app-mcp` 包 |
| `Kwrt/devices/mediatek_filogic/.config` | 追加 `CONFIG_PACKAGE_mcp-routerd=y`（+ 二期 `luci-app-mcp`） | 与 WR30U 设备选择同文件，一次构建带上 |
| `Kwrt/devices/common/diy.sh` | 不变（feed 已指向 op-packages） | 前提是 op-packages 精简版已推 fork 并生效（见方案 §11.3） |
| nginx 配置（包内 files） | `location /mcp { proxy_pass http://127.0.0.1:8765; }` | 或独立端口监听 br-lan，二选一 |

构建侧无新概念：就是「再加一个自研包 + 一行 CONFIG」，走既有 GitHub Actions 流水线（`workflow_dispatch` 触发 `mediatek_filogic`）即可带出。

---

## 9. 执行步骤清单（建议分三期）

### Phase 0 —— 立即可做（零固件改动，验证工具面）
1. PC 上跑 `npx @subashgautam/router-mcp serve`（或 mcp-openwrt）指向 WR30U，SSH root 登录；
2. 在 WorkBuddy 的 `~/.workbuddy/mcp.json` 加 `type: "http"/"stdio"` 条目接入；
3. 试跑典型任务：「列出在线设备」「扫一下周边 WiFi」「把 5G 信道换到干扰小的」——**记录哪些工具真有用**，作为固件版工具面的依据。

### Phase 1 —— 固件内置只读 MCP（核心交付）
1. `op-packages` 新建 `mcp-routerd` 包（ucode，L0 工具集 + token + 审计，~800 行）；
2. `devices/mediatek_filogic/.config` 启用；推 `jianjunx/jwrt`，`workflow_dispatch` 触发构建；
3. 刷机验证：`curl -H "Authorization: Bearer $TOKEN" https://192.168.1.1/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`；
4. Agent 端接入（WorkBuddy `mcp.json`）跑通只读任务。

### Phase 2 —— 写操作 + LuCI 管理页
1. L1 工具（WiFi/DHCP/防火墙/重启）+ `confirm` 二次确认 + uci 开关；
2. `luci-app-mcp`：开关、工具分级、token 轮换、审计日志页；
3. 网络类写操作加 60s 自动回滚窗口。

### Phase 3（可选）—— 进阶
- resources/prompts（配置快照、故障诊断引导）、异步长任务、WireGuard 远程访问场景下的 mTLS。

---

## 10. 附录

### A. 客户端接入示例（WorkBuddy / 通用 MCP 客户端）

```json
{
  "mcpServers": {
    "wr30u-router": {
      "type": "http",
      "url": "https://192.168.1.1/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

### B. `/etc/config/mcp` 示例

```
config mcp 'global'
    option enabled '1'
    option listen '127.0.0.1:8765'
    option token '<首次启动生成的随机值>'
    option allow_write '0'      # L1 写工具
    option allow_exec '0'       # L2 恒为 0，不实现
    option audit '1'
```

### C. 参考资料
- MCP 规范（含 2026-07-28 修订说明）：https://modelcontextprotocol.io/specification
- Streamable HTTP 传输：单端点 POST JSON-RPC，可选 SSE
- ramanarupa/mcp-openwrt、subashgautam/router-mcp（工具面与安全分层参考）
- OpenWrt 论坛 Oasis/OLT：路由器侧工具层的 ubus 实现先例
- 本项目前置文档：《Kwrt精简固件方案.md》（构建体系、op-packages feed、风险清单）

---

## 11. 落地执行记录（2026-07）

按 §8 集成方案落地为独立包 `mcp-routerd`，提交至 Kwrt 仓库
`devices/common/diy/package/mcp-routerd/`（`diy/` 覆盖层机制，无需 fork feed）。

### 交付物
| 文件 | 说明 |
|---|---|
| `Makefile` | OpenWrt 包定义，`DEPENDS` 引入 ucode 及 ubus/uci/socket/fs/log 模块 |
| `files/usr/bin/mcp-routerd` | 主程序（约 560 行 ucode），HTTP + JSON-RPC 2.0 + 工具层 |
| `files/etc/init.d/mcp-routerd` | procd 守护（`START=95`，respawn） |
| `files/etc/config/mcp` | UCI 默认配置（listen/token/allow_write/audit） |
| `files/etc/uci-defaults/99-mcp-routerd` | 首次启动生成 token、写 banner 提示、修复 nginx include |
| `files/etc/nginx/conf.d/mcp-routerd.locations` | `/mcp` 反代注入（沿用 `_lan` 的 LAN 限制） |

### 实现要点（与 §6 的偏差说明）
- **协议**：仅实现稳定核心子集 `initialize`/`ping`/`tools/list`/`tools/call`；
  `notifications/*` 返回 202 空体；支持批量数组；`initialize` 回显客户端
  `protocolVersion`，缺省回退 `2025-06-18`。
- **工具面**：共 14 个 —— 8 个 L0 只读（system_info/network_status/wifi_status/
  wifi_scan/list_clients/firewall_rules/dns_query_log/read_log）+ 6 个 L1 写
  （set_wifi/set_dhcp_lease/add_firewall_rule/remove_firewall_rule/
  restart_service/reboot）。比 §6.2 初稿多拆出 `remove_firewall_rule`。
- **写防护**：`uci mcp.global.allow_write=1` 开关 **且** `arguments.confirm: true`
  双重校验，缺一即拒绝并审计。L2 原始 shell 按方案不实现。
- **审计**：每次 tools/call 经 `log.syslog` 记录（printf 格式对不可信参数用 `%s`
  包裹，防格式串问题）；软失败 `{error: ...}` 归一化为协议级 `isError: true`。
- **部署**：监听默认 `127.0.0.1:8765`；nginx-ssl 的 `_lan` server 自动 include
  `conf.d/*.locations`，故单文件即完成 `/mcp` 反代挂载，HTTPS 与 LAN 限制免费获得。

### 验证
- 本地构建 ucode 解释器 + fs/socket/log 模块，ubus/uci 以 stub 模块替代，
  完整跑通协议路径：鉴权（401）→ initialize → tools/list → tools/call
  → 写工具双重拦截 → 通知 202 → 批量请求 → 解析错误 -32700 → 405/404。全部通过。
- `ucode -c` 编译检查通过。
- Git 侧以 `.gitattributes` 强制 `eol=lf`，规避 Windows autocrlf 破坏脚本换行。

### 构建与客户端接入
- 提交 `b804cfb4`（Kwrt 仓库 → jianjunx/jwrt `main`），GitHub Actions
  `Openwrt-AutoBuild.yml`（target=mediatek_filogic，param=nocache）已触发，
  run 39646379803。
- 固件刷入后：token 在 `/etc/config/mcp` 或 `logread | grep mcp-routerd` 获取，
  客户端配置见附录 A。

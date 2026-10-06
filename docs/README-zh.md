# W1MER

*Only one Writer, Many Explorers Read*

[English](../README.md) | **中文**

**W1MER** 是一套面向共享代码库上长程自主运行的 Agent 团队的编排范式。

命名取自经典的 SWMR（Single Writer / Multiple Readers，单写多读）并发模型，以两条硬约束限制多 Agent 并行场景：

- **同一时刻只有一个写入者。** 唯一的一个实现者独占编译与提交（编译互斥）。任意两个 Agent 不会并发构建或写码。
- **多个读者并行。** 探索者与验证者只读，并行放批执行——调查与审查从不等待编译器。

## 为什么我自己造轮子？

市面上已有的大多数多 Agent 框架（GSD、orchestrator-workers、……）都保持着“并行数至上”的理念，允许多个拥有写权限的 Agent 并发执行。

然而，在我用它们去跑 Rust 项目的时候，这套理念成了累赘——多个 Agent 相互抢占编译和测试，CPU 占用率居高不下，甚至还给我电脑搞关机了几次！一番折腾下来，东西没做多少，Token 浪费不少！

不难发现，在编译型语言（Rust、C++ 等）的项目中，想要同时保持开发高效、Token 节约、执行顺畅这三者绝非易事，当然，这也不是“不可能三角”！

我根据我开发大型 Rust 项目的经验，总结出了一套完整的编译型多 Agent 开发框架，在保持编译单一持有的同时，并发去做只读操作，尽可能高效地利用了空等待的时间，让 Agent **长程自主进行编译型项目的高效开发** 成为了可能！

## 核心机制

### 三并发流水线调度

项目的开发被分为多个 **主批次** 和 **次批次**，每个批次以实现一个任务为中心。

通常情况下，Agent 在 **主批次** 下运行，进行三并发调度。

每个 **主批次** 并行运行三个 Agent，各处理不同的职责：

```
Reviewer（验证者）：回审上一批完成的任务（只读，写审查结果）
Implementer（实现者）：完成当前任务（唯一写入者；编译并提交）
Explorer（探索者）：调查下一任务（只读，写方案）
```

> 文雅一点地说，就是一个批次内同时包含“过去”、“现在”和“未来” XD

### 审查独立性与修复闭环

在 **主批次** 遇到异常情况时，开发进入 **次批次**，单独派一个 Fixer（修复者）：

1. Reviewer 发现问题时，Fixer 修复 Reviewer 发现的问题并补充审查文档；
2. Implementer 异常中断时，Fixer 实现 Implementer 做了一半的功能并汇报。

修复完成后，回归 **主批次**，此时的 Reviewer 就是审查 Fixer 的修复结果了。

> 根据问题的复杂度不同，有可能出现 **次批次** 套 **次批次** 的情况（Fixer 修错时）

### 中断恢复

异常退出 / 空回报的 Agent 视为*阶段未完成*，按不同的角色进行恢复：

1. Explorer/Reviewer：重新启动
2. Implementer：先看工作区
    - 干净（没有做一半的工作）：重新启动 Implementer 重做该任务
    - 脏（存在做一半的工作）：派一个 Fixer Agent 去补完做一半的工作，同时承担 Implementer 的汇报职责
3. Fixer：派一个 Fixer 继续修复

### 长期压缩上下文环境下的人格保持

实际使用中，AI 会在多次压缩上下文后逐渐丢失角色，最终导致循环崩溃，因此需要有一个程序化流程来对其进行约束。

在使用 CLI 工具时，AI 会被重新提示要求手动开启对应批次，并核对未开启 Agent 名单，在批次结束后也需要手动关闭。

这一过程有效提高了主 Agent 在长线开发中的人格保持度，经测试，角色保持从约连续 7 次压缩循环提升至如今的 **理论上无限次**。

### 滚动层级编号

任务使用无上限的滚动数字编号（`05`、`05.1`、`05.1.1`、……）。任意节点可按需追加。

例如：验证者发现后续问题就追加 `05.1`、`05.1.1`，保持编号的交叉引用稳定。

排序为前序遍历（父在前子紧跟）。

### 规范化的系列文档处理模式

*与其让 AI 读不断追加的单一文档，不如以“单索引+多详细文档”方式分离*

这套模式是我在开发中归纳出来的，因此我认为对于任意的系列文档都可以用上，包括但不限于性能表现、审查报告、实现结果、Bug 根因等。

W1MER 提供了一套可复用的系列文档操作方式，把这种复杂但高效的模式的增查删改封装起来，从创建系列文档、新增新内容，到重建索引，都变成了一个轻量的 CLI 工具。

每个 Agent 不再需要一个个去读怎么维护这套架构，而是通过 CLI 工具直接参与其中，做到长期不崩溃、持续高可用。

任务本身被划分为 `todo`, `done`, `reviewed`, `issue` 四类：

- `todo`: 尚未实现的待做项
- `done`: 已完成但未经审查的项
- `reviewed`: 经审查无误后可以保持不变的项
- `issue`: 经审查发现问题，等待 Fixer 在再次实现的项

### 账单友好的分层 codebase 文档

在项目的持续迭代过程中，传统的“一次 codebase，到处使用”反而会增加噪音。

我认为，codebase 应该是保持整体长期不变，细节持续更新的，这样才能帮助 Agent 更好理解当前进度，而不是回头去看过时的文档，反而还需要补充更多的源码。

W1MER 的 codebase 文档分两层：

- **稳定层**：长期存活的项目地图，设计较少变化，且根据模块进行文档拆分，避免更新时需要跨文档更新，也降低了重建时的输入与输出成本和模型上下文要求；overview 文档（`STACK → STRUCTURE → CONVENTIONS`）保持固定阅读顺序，形成稳定的 prompt 前缀以命中上下文缓存；
- **动态层**：与稳定层完全隔离，形成一套系列文档体系，也可以用 CLI 工具快速查询。

每个 **主批次** 中，Reviewer 还承担着观察架构变化的职责，在审查文档里总结一栏简要的架构变化摘要。

架构文档的更新定期进行，把增量合并进稳定文档并清空变更日志。

## 安装

skill 包是自包含的——安装到宿主机的 skill 目录（如 `npx skills add pjh456/w1mer -a opencode -g`，或直接复制 `w1mer/` 目录），然后注册 host agents 与 CLI：

```sh
python3 <skill-dir>/scripts/w1mer.py install [--host opencode|claude-code|codex|all]
                                            [--link] [--bin-dir <dir>]
```

`install` 会把各 host 的 agent 定义复制到对应 agent 目录（opencode → `~/.config/opencode/agents/`，claude-code → `~/.claude/agents/`，codex → `~/.codex/agents/`），并在 PATH 上安装 `w1mer` 启动器（`--link` 改为 symlink 指向 skill 脚本）。`install --list` 只显示目标位置、不改动任何东西。Windows 上启动器为转发到 skill 脚本的 `.bat`。Codex 以 TOML 读取 agent，并会自动从 `~/.agents/skills/`（USER 作用域）发现 skill 本体。

## 项目布局

```
W1MER/
├── README.md           # 本文件（英文版）
├── docs/               # 范式与操作规范
│   └── README-zh.md    # 中文版 README
└── w1mer/              # skill 包（自包含；整体复制即可安装）
    ├── SKILL.md        #   skill 入口（Anthropic Agent Skills 格式）
    ├── references/     #   角色 / 调度 / 归档 / codebase / 映射规范
    ├── templates/      #   .w1mer/ 脚手架 + schema.yaml 注册表
    ├── scripts/        #   w1mer.py CLI（init/install/new/set/defer/re-rank/list/build/sync，批次生命周期 batch-start/batch-end/ensure/role-join/show/status）
    └── hosts/          #   宿主特定的 agent 定义
        ├── opencode/   #     .opencode/agent/*.md（install → ~/.config/opencode/agents/）
        ├── claude-code/    #   .claude/agents/*.md
        └── codex/      #     .codex/agents/*.toml（install → ~/.codex/agents/）
```

## 状态

已经投入实际应用环境，并在下列项目中参与长期开发维护：

- [OxideJS](https://github.com/pjh456/OxideJS/): 基于 Rust 的 JavaScript 执行引擎
- [pjh_cli](https://github.com/pjh456/pjh_cli/): 基于 C++20 的跨平台 CLI 工具集

## 常见问题

### W1MER 是否会影响其他 Skill 使用？

W1MER 只约束了项目的开发规范，不会对知识类 Skill 造成影响。但不建议与 Oh-My-Opencode 等自定义编排流程的 Skill 结合使用，该操作属于未经测试与兜底的未定义行为。

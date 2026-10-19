# Poverty Bank A2 — optimized prototype

本版本在独立 Docker project、数据库和 volume 中运行，不覆盖原版。

## 启动 / Run

在本目录执行：

```sh
docker compose up --build -d --wait web
```

打开 http://localhost:18080 。首次启动会自动建表、生成数据、集成、验证并创建只读报表账号。
PostgreSQL：localhost:15434，database `poverty_bank`，user/password `student`（仅本地教学）。
网页使用权限受限的 `report_reader`，并非 student 超级用户。端口仅绑定 localhost。

## 已实现 / Features

- 四个模拟业务来源：Deposits、Cards、Lending、CRM；同一 PostgreSQL 内不同 source schemas，不宣称为四个实际部署的外部系统。
- 原始数据 → staging/身份匹配 → Data Vault 与维度仓库两条加载路径 → 三个 dashboard。CRM consent/interactions 经身份映射进入仓库。
- 120 条来源客户档案（Deposits 39、Cards 30、Lending 14、CRM 37）整合为 50 个 canonical identities（不等于 50 个已确认的独立自然人）；92 个账户、1,968 笔存款/卡交易、62 笔还款、92 条 CRM 活动。交易覆盖 2026-07-01 至 2026-10-15 的 107 天。
- Customer 360：搜索、身份筛选、分页、产品分布图、consent 状态、最近 20 条交易/还款/CRM 活动、来源身份明细。
- Transaction Monitoring：日期预设、客户/标记筛选、每日交易趋势、分页、逐客户逐日钻取底层交易。
- Product Review：建议类别图、可解释的规则证据；排除 CLOSED 持有状态；身份冲突/未匹配或无账户数据时不推荐产品。
- 三个报告支持筛选后 CSV（包括全部匹配页）和 full report CSV；页面更新时间与最近成功验证的数据加载时间分开显示。
- 缺 email、同 email 不同生日、CRM-only、不同产品组合、关闭账户、不同渠道/商户、CDR 撤回及历史事件等场景均保留。

数据为可重复生成的合成数据。余额为设定的当前快照，交易是示例活动，不是完整流水账，不能用来重建余额历史。
REVIEW 是课堂阈值规则，不是反洗钱模型、欺诈结论、信贷决策或真实营销许可。CDR consent 不等于 marketing consent。
身份匹配采用规范化 email，DOB 冲突隔离；不是生产级 KYC。

## 检查 / Verify

```sh
docker compose exec -T web python scripts/verify_pipeline.py
docker compose exec -T web python scripts/smoke_reports.py
docker compose exec -T web env TMPDIR=/dev/shm python -m unittest discover -s tests -p 'test_*api.py' -v
docker compose exec -T web python -m unittest discover -s tests -p 'test_consent_sql.py' -v
```

数据验证将原始来源与身份映射、事实表、钻取明细及汇总独立核对，而不只检查 SQL 是否执行成功。
SQL 回归测试创建随机命名的临时数据库，结束时仅删除它自己创建的测试库，不操作主库。

重复加载：`docker compose run --rm python python run_sql.py`。稳定业务键阻止事实重复插入；Vault 保留加载历史。
导出运行日志和三份 CSV：`bash scripts/run_pipeline.sh`。
打包源码：`python scripts/package_release.py`，不包含数据库 volume、个人路径、密钥或本机设置。

## 独立质量演示 / Isolated quality cases

```sh
docker compose -f compose.quality.yaml up --build -d --wait web-identity web-consent web-types
docker compose -f compose.quality.yaml run --rm quality-cli python scripts/quality_demo.py status all
```

Identity / Consent events / Consent types：分别为 http://localhost:18085 、http://localhost:18086 、http://localhost:18087 。
质量库端口 15435；各场景使用原始小型基线，便于观察注入前后变化，不混入扩容数据。
在这些新端口的独立环境使用 `inject`、`run`、`verify`、`reset`（例如 `... quality_demo.py inject identity`）；先查 status。
本目录配置不改动原版 18081–18083 的数据库或 volume。

## 演示路线 / Demo route

1. Customer 360 展示总量与来源映射；选择一个多产品客户，展示活动时间线。
2. 从客户打开交易报告，选一天，核对总数/金额与逐笔记录。
3. 全历史与近 7/30 天切换，观察趋势图和汇总变化；导出 filtered CSV。
4. Product Review 查看不同建议及解释，再展示身份冲突、CRM-only 与 closed-card 案例。
5. 必要时使用独立质量环境展示故障注入、重新加载、验证与恢复。

停止但保留数据：`docker compose stop`。不要使用 `down -v`，除非明确希望删除本版本数据。

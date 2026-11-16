# 类别版本与迁移运维

类别现在由 `category_concepts.code` 标识；显示名按 `(concept, locale, version)` 存于
`category_terms`，同义名/旧名存于 `category_aliases`。逐票保存 `category_code`，
研究保存声明顺序表 `category_scheme`，每次运行保存 `category_snapshot`（编码 + 名称 +
语言 + 版本）。运行结果、冻结快照和已批准说明里的显示名不会被后续改名改写。

## 升级既有库

```sh
# 1. 只追加列，可重复执行；不改写已有行
docker compose exec -T backend python -c "from src.config.database import engine; from src.services.schema import ensure_category_columns as e; print(e(engine))"
# 2. 回填编码与隔离记录；按研究逐个提交，可重复执行
docker compose exec -T backend python -m scripts.migrate_categories
```

`migrate_categories` 输出 `migrated/unchanged/partial/failed/votes_coded/quarantined`
与 `verification`。重复执行应为 `migrated=0`、编码数不变。

## 迁移规则

- 已迁移（`category_scheme` 完整且逐票有编码）的研究不写任何行。
- 未知名称与重复/歧义映射写入 `category_quarantine`，保留原名称与原因码
  （`unknown_category` / `ambiguous_mapping` / `unmapped_concept` / `duplicate_mapping` /
  `migration_error`），不会静默丢弃，也不伪造映射。
- 单个研究失败只回滚该研究；再次执行从失败处继续，不重复创建概念/同义名。
- 回读旧运行和旧说明时只补只读元数据（`category_entries`），不持久化改写。

## API

|方法|路径|权限|说明|
|---|---|---|---|
|GET|`/categories`|登录|编码、各语言当前名、同义名、版本历史|
|POST|`/categories/{code}/rename`|admin|追加新版本；仅影响之后创建的新运行|
|POST|`/studies/{id}/aliases`|admin/analyst|登记同义名；与其他编码冲突时 422|
|GET|`/categories/quarantine`|登录|隔离队列与原因|
|POST|`/categories/quarantine/{id}/resolve`|admin|标记处置结果，保留原记录|
|POST|`/categories/migrate`|admin|执行迁移|
|GET|`/categories/verify`|登录|逐研究核对编码覆盖率、重复编码、待隔离数|
|GET|`/memos/{id}/export?locale=zh-CN\|en`|登录|按冻结版本输出名称与编码，并标注翻译状态|

导入时 `POST /studies?locale=` 指定新概念的显示语言；`POST /runs` 的
`category_locale` 冻结该次运行的名称语言。导出 `locale` 缺省取原始快照名，
`translation_status` 会区分 `translated` / `locale_fallback` / `original` / `no_code`。

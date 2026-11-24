# 初始结构

`src/models/tables.py` 定义初始关系结构。API 启动时显式创建演示库的表；此版本只包含初始 schema。需要在已有部署升级表结构时，应单独引入版本化迁移。

## 选月更正补核单（`supplement_orders`）

新增补核单表与部分唯一索引。全新演示库由启动时的 `schema.create_all` 与 `ensure_supplement_indexes` 自动创建；已有部署升级时按下列 DDL 手工执行：

```sql
CREATE TABLE supplement_orders (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES business_cases(id),
    basis_evidence_id INTEGER NOT NULL REFERENCES business_evidence(id),
    month VARCHAR(7) NOT NULL,
    corrected_state VARCHAR(20) NOT NULL,
    report JSON NOT NULL,
    digest VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    superseded_by INTEGER REFERENCES supplement_orders(id),
    owner VARCHAR(40) NOT NULL,
    reviewer VARCHAR(40),
    review_note VARCHAR(500),
    reviewed_at VARCHAR(32),
    created_at VARCHAR(32) NOT NULL,
    review_result JSON,
    CONSTRAINT ck_supplement_status CHECK (status IN ('effective', 'superseded')),
    CONSTRAINT uq_supplement_request UNIQUE (case_id, month, digest)
);
CREATE INDEX ix_supplement_orders_case_id ON supplement_orders (case_id);
CREATE INDEX ix_supplement_orders_status ON supplement_orders (status);
-- 同一档案同一月份至多一单生效
CREATE UNIQUE INDEX uq_supplement_effective_month
    ON supplement_orders (case_id, month) WHERE status = 'effective';
```


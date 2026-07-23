## ADDED Requirements

### Requirement: 跨页合并不做大小上限
同结构表格合并时不检查合并后的总字符数。

#### Scenario: 大表格跨多页合并
- **WHEN** 同一个表格跨越 3 页以上，每页表头结构相同，中间仅夹带页码等短文本
- **THEN** 全部合并为一个 table segment，后续通过行级切分处理

### Requirement: 大表格按行边界切分
合并后的表格超过 2000 字符时，按行边界切分，每块复制表头。

#### Scenario: 大表格行级切分
- **WHEN** table segment 字符数 > `TABLE_ROW_CHUNK_CHARS`（默认 2000）
- **THEN** 以数据行边界分组，每组前复制表头行+分隔行，每组不超过 `TABLE_ROW_CHUNK_CHARS`

#### Scenario: 小表格保持完整
- **WHEN** table segment 字符数 ≤ `TABLE_ROW_CHUNK_CHARS`
- **THEN** 不做切分，保持完整

### Requirement: 残差短文本合并到相邻表格
不足 200 字符的独立文本段，如果与 TABLE segment 相邻，粘到表格上。

#### Scenario: 表格后的短文本
- **WHEN** text segment < `ORPHAN_THRESHOLD_CHARS`（默认 200）且后一个 segment 是 TABLE
- **THEN** 短文本粘到 TABLE 开头作为前导说明

#### Scenario: 表格前的短文本
- **WHEN** text segment < `ORPHAN_THRESHOLD_CHARS`（默认 200）且前一个 segment 是 TABLE
- **THEN** 短文本粘到 TABLE 末尾作为备注

#### Scenario: 前后都不是表格的短文本
- **WHEN** text segment < `ORPHAN_THRESHOLD_CHARS` 但前后都不是 TABLE
- **THEN** 不做合并，保持独立 text segment

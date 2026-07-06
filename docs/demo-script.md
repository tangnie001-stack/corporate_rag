# MVP Demo Script

## Prerequisites
- Docker Compose running (`docker compose up -d`)
- Test PDF documents: `贵州茅台2024年年报.pdf`, `厦门灿坤2019年年报.pdf`
- OpenSpec change `mvp-core-features` fully implemented

## Step-by-Step

### 1. Create Knowledge Base
1. Open http://localhost in browser
2. Click "Create Knowledge Base"
3. Name: `finance-demo`
4. Confirm KB is created and visible in KB selector

### 2. Upload Documents
1. Go to Documents page
2. Upload both test PDFs
3. Wait for processing to complete (check status badges)

### 3. Test RAGAS Evaluation
```bash
python -m src.eval_ragas --check
# Expected: QA pair count: 22 (OK)

python -m scripts.compare_chunk
# Expected: Report saved to data/reports/chunk_comparison.md

python -m src.eval_ragas --gate
# Expected: All metrics pass thresholds, exit 0
```

### 4. Test Chat QA
1. Select `finance-demo` KB
2. Ask 5 representative questions:
   - "2024年贵州茅台营业收入是多少？"
   - "2024年基本每股收益是多少？"
   - "厦门灿坤2019年主营业务收入是多少？"
   - "贵州茅台国内国外收入占比如何？"
   - "前十大股东持股情况如何？"
3. Verify: streaming response, correct figures, citation source displayed

### 5. Test Edge Cases
1. Ask "你好" — expect short query warning "查询内容过短"
2. Ask about nonexistent KB — expect error message
3. Check session history sidebar — past conversations listed

### Expected Outcomes
- All RAGAS metrics pass quality gate
- QA responses contain accurate financial figures with citations
- Error states show user-friendly Chinese messages
- Session history persists across page reloads

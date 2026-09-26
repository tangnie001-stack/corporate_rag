---
name: market-sizing-analysis
description: 测算市场空间（TAM/SAM/SOM）：自上而下、自下而上与价值理论三种方法及交叉验证。当用户要求测算某行业/业务的市场规模与可及空间、验证市场机会、或为业务规划估算可获取份额时使用。材料须由主 agent 预检索一并传入 task。
context: fork
---


# Market Sizing Analysis

Comprehensive market sizing methodologies for calculating Total Addressable Market (TAM), Serviceable Available Market (SAM), and Serviceable Obtainable Market (SOM) for startup opportunities.

## Overview

Market sizing provides the foundation for startup strategy, fundraising, and business planning. Calculate market opportunity using three complementary methodologies: top-down (industry reports), bottom-up (customer segment calculations), and value theory (willingness to pay).

## Core Concepts

### The Three-Tier Market Framework

**TAM (Total Addressable Market)**

- Total revenue opportunity if achieving 100% market share
- Defines the universe of potential customers
- Used for long-term vision and market validation
- Example: All email marketing software revenue globally

**SAM (Serviceable Available Market)**

- Portion of TAM targetable with current product/service
- Accounts for geographic, segment, or capability constraints
- Represents realistic addressable opportunity
- Example: AI-powered email marketing for e-commerce in North America

**SOM (Serviceable Obtainable Market)**

- Realistic market share achievable in 3-5 years
- Accounts for competition, resources, and market dynamics
- Used for financial projections and fundraising
- Example: 2-5% of SAM based on competitive landscape

### When to Use Each Methodology

**Top-Down Analysis**

- Use when established market research exists
- Best for mature, well-defined markets
- Validates market existence and growth
- Starts with industry reports and narrows down

**Bottom-Up Analysis**

- Use when targeting specific customer segments
- Best for new or niche markets
- Most credible for investors
- Builds from customer data and pricing

**Value Theory**

- Use when creating new market categories
- Best for disruptive innovations
- Estimates based on value creation
- Calculates willingness to pay for problem solution

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.


## 本项目的数据来源约定

本节替代上游 `references/data-sources.md`（那是面向创业公司的外部数据源清单：行业研究机构、政府公开数据、B2B 数据库等）。

在本 harness 中：

- 测算所依据的一切事实性数据，只能来自**本轮检索到的材料**（知识库片段 / 联网结果），并按其来源编号标注；不得引用未检索到的外部机构数据（如第三方研究机构的份额数字）。
- 上游 Step 2「采集数据源」在本项目落实为：**从已传入的材料中抽取市场规模、增长、份额、单价、客户数等口径**；材料缺口按下方「证据不足」处理。
- 材料不足以支撑某个层级（典型是 SAM/SOM 需要的细分口径）时，**明示缺口**并说明该缺口会对结论产生什么影响，不得用假设数字填平后当作已披露事实。
- 若某项数字是**本方在已披露口径上的推算**（而非材料直接给出），必须显式标注为推算并写出算式。

## 详细流程与算例（来自上游 references/details.md）



## Three-Methodology Framework

### Methodology 1: Top-Down Analysis

Start with total market size and narrow to addressable segments.

**Process:**

1. Identify total market category from research reports
2. Apply geographic filters (target regions)
3. Apply segment filters (target industries/customers)
4. Calculate competitive positioning adjustments

**Formula:**

```
TAM = Total Market Category Size
SAM = TAM × Geographic % × Segment %
SOM = SAM × Realistic Capture Rate (2-5%)
```

**When to use:** Established markets with available research (e.g., SaaS, fintech, e-commerce)

**Strengths:** Quick, uses credible data, validates market existence

**Limitations:** May overestimate for new categories, less granular

### Methodology 2: Bottom-Up Analysis

Build market size from customer segment calculations.

**Process:**

1. Define target customer segments
2. Estimate number of potential customers per segment
3. Determine average revenue per customer
4. Calculate realistic penetration rates

**Formula:**

```
TAM = Σ (Segment Size × Annual Revenue per Customer)
SAM = TAM × (Segments You Can Serve / Total Segments)
SOM = SAM × Realistic Penetration Rate (Year 3-5)
```

**When to use:** B2B, niche markets, specific customer segments

**Strengths:** Most credible for investors, granular, defensible

**Limitations:** Requires detailed customer research, time-intensive

### Methodology 3: Value Theory

Calculate based on value created and willingness to pay.

**Process:**

1. Identify problem being solved
2. Quantify current cost of problem (time, money, inefficiency)
3. Calculate value of solution (savings, gains, efficiency)
4. Estimate willingness to pay (typically 10-30% of value)
5. Multiply by addressable customer base

**Formula:**

```
Value per Customer = Problem Cost × % Solved by Solution
Price per Customer = Value × Willingness to Pay % (10-30%)
TAM = Total Potential Customers × Price per Customer
SAM = TAM × % Meeting Buy Criteria
SOM = SAM × Realistic Adoption Rate
```

**When to use:** New categories, disruptive innovations, unclear existing markets

**Strengths:** Shows value creation, works for new markets

**Limitations:** Requires assumptions, harder to validate

## Step-by-Step Process

### Step 1: Define the Market

Clearly specify what market is being measured.

**Questions to answer:**

- What problem is being solved?
- Who are the target customers?
- What's the product/service category?
- What's the geographic scope?
- What's the time horizon?

**Example:**

- Problem: E-commerce companies struggle with email marketing automation
- Customers: E-commerce stores with >$1M annual revenue
- Category: AI-powered email marketing software
- Geography: North America initially, global expansion
- Horizon: 3-5 year opportunity

### Step 2: Gather Data Sources

Identify credible data for calculations.

**Top-Down Sources:**

- Industry research reports (Gartner, Forrester, IDC)
- Government statistics (Census, BLS, trade associations)
- Public company filings and earnings
- Market research firms (Statista, CB Insights, PitchBook)

**Bottom-Up Sources:**

- Customer interviews and surveys
- Sales data and CRM records
- Industry databases (LinkedIn, ZoomInfo, Crunchbase)
- Competitive intelligence
- Academic research

**Value Theory Sources:**

- Customer problem quantification
- Time/cost studies
- ROI case studies
- Pricing research and willingness-to-pay surveys

### Step 3: Calculate TAM

Apply chosen methodology to determine total market.

**For Top-Down:**

1. Find total category size from research
2. Document data source and year
3. Apply growth rate if needed
4. Validate with multiple sources

**For Bottom-Up:**

1. Count total potential customers
2. Calculate average annual revenue per customer
3. Multiply to get TAM
4. Break down by segment

**For Value Theory:**

1. Quantify total addressable customer base
2. Calculate value per customer
3. Estimate pricing based on value
4. Multiply for TAM

### Step 4: Calculate SAM

Narrow TAM to serviceable addressable market.

**Apply Filters:**

- Geographic constraints (regions you can serve)
- Product limitations (features you currently have)
- Customer requirements (size, industry, use case)
- Distribution channel access
- Regulatory or compliance restrictions

**Formula:**

```
SAM = TAM × (% matching all filters)
```

**Example:**

- TAM: $10B global email marketing
- Geographic filter: 40% (North America)
- Product filter: 30% (e-commerce focus)
- Feature filter: 60% (need AI capabilities)
- SAM = $10B × 0.40 × 0.30 × 0.60 = $720M

### Step 5: Calculate SOM

Determine realistic obtainable market share.

**Consider:**

- Current market share of competitors
- Typical market share for new entrants (2-5%)
- Resources available (funding, team, time)
- Go-to-market effectiveness
- Competitive advantages
- Time to achieve (3-5 years typically)

**Conservative Approach:**

```
SOM (Year 3) = SAM × 2%
SOM (Year 5) = SAM × 5%
```

**Example:**

- SAM: $720M
- Year 3 SOM: $720M × 2% = $14.4M
- Year 5 SOM: $720M × 5% = $36M

### Step 6: Validate and Triangulate

Cross-check using multiple methods.

**Validation Techniques:**

1. Compare top-down and bottom-up results (should be within 30%)
2. Check against public company revenues in space
3. Validate customer count assumptions
4. Sense-check pricing assumptions
5. Review with industry experts
6. Compare to similar market categories

**Red Flags:**

- TAM that's too small (< $1B for VC-backed startups)
- TAM that's too large (unsupported by data)
- SOM that's too aggressive (> 10% in 5 years for new entrant)
- Inconsistency between methodologies (> 50% difference)

## Industry-Specific Considerations

### SaaS Markets

**Key Metrics:**

- Number of potential businesses in target segment
- Average contract value (ACV)
- Typical market penetration rates
- Expansion revenue potential

**TAM Calculation:**

```
TAM = Total Target Companies × Average ACV × (1 + Expansion Rate)
```

### Marketplace Markets

**Key Metrics:**

- Gross Merchandise Value (GMV) of category
- Take rate (% of GMV you capture)
- Total transactions or users

**TAM Calculation:**

```
TAM = Total Category GMV × Expected Take Rate
```

### Consumer Markets

**Key Metrics:**

- Total addressable users/households
- Average revenue per user (ARPU)
- Engagement frequency

**TAM Calculation:**

```
TAM = Total Users × ARPU × Purchase Frequency per Year
```

### B2B Services

**Key Metrics:**

- Number of target companies by size/industry
- Average project value or retainer
- Typical buying frequency

**TAM Calculation:**

```
TAM = Total Target Companies × Average Deal Size × Deals per Year
```

## Presenting Market Sizing

### For Investors

**Structure:**

1. Market definition and problem scope
2. TAM/SAM/SOM with methodology
3. Data sources and assumptions
4. Growth projections and drivers
5. Competitive landscape context

**Key Points:**

- Lead with bottom-up calculation (most credible)
- Show triangulation with top-down
- Explain conservative assumptions
- Link to revenue projections
- Highlight market growth rate

### For Strategy

**Structure:**

1. Addressable customer segments
2. Prioritization by opportunity size
3. Entry strategy by segment
4. Expected penetration timeline
5. Resource requirements

**Key Points:**

- Focus on SAM and SOM
- Show segment-level detail
- Connect to go-to-market plan
- Identify expansion opportunities
- Discuss competitive positioning

## Common Mistakes to Avoid

**Mistake 1: Confusing TAM with SAM**

- Don't claim entire market as addressable
- Apply realistic product/geographic constraints
- Be honest about serviceable market

**Mistake 2: Overly Aggressive SOM**

- New entrants rarely capture > 5% in 5 years
- Account for competition and resources
- Show realistic ramp timeline

**Mistake 3: Using Only Top-Down**

- Investors prefer bottom-up validation
- Top-down alone lacks credibility
- Always triangulate with multiple methods

**Mistake 4: Cherry-Picking Data**

- Use consistent, recent data sources
- Don't mix methodologies inappropriately
- Document all assumptions clearly

**Mistake 5: Ignoring Market Dynamics**

- Account for market growth/decline
- Consider competitive intensity
- Factor in switching costs and barriers


## Quick Start

To perform market sizing analysis:

1. **Define the market** - Problem, customers, category, geography
2. **Choose methodology** - Bottom-up (preferred) or top-down + triangulation
3. **Gather data** - Industry reports, customer data, competitive intelligence
4. **Calculate TAM** - Apply methodology formula
5. **Narrow to SAM** - Apply product, geographic, segment filters
6. **Estimate SOM** - 2-5% realistic capture rate
7. **Validate** - Cross-check with alternative methods
8. **Document** - Show methodology, sources, assumptions
9. **Present** - Structure for audience (investors, strategy, operations)

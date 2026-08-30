# pdf-parsing Specification (Delta)

## ADDED Requirements

### Requirement: Cross-line table cell integrity
PDF table extraction SHALL flatten cell-internal newlines so a wrapped cell stays within a single row, keeping its value column attached to its label. The full-text channel SHALL be the authoritative source for chunk content and table rows.

#### Scenario: Wrapped table cell keeps value column
- **WHEN** a PDF table has a cell wrapping across two lines (e.g. label `购建固定资产、无形资产和其他` + `长期资产支付的现金`, value `63,134,713`)
- **THEN** the extracted table row SHALL contain the full label and its value on the same row (`购建固定资产、无形资产和其他长期资产支付的现金 | 63,134,713`), so a table-preserving chunk does not split label from value

#### Scenario: Table cells with embedded newlines become single-line
- **WHEN** `find_tables().extract()` returns a cell containing `\n`
- **THEN** the cell SHALL be rendered as a single line with the newline replaced by a space, without breaking the Markdown `|...|` row structure

### Requirement: Table extraction immune to heading-channel imports
PDF table extraction SHALL NOT be affected by the presence or absence of the heading-extraction library in the same process. If the heading-extraction library modifies global extraction state at import time, the heading channel SHALL run in an isolated process so that the full-text channel's table cell integrity is preserved.

#### Scenario: Heading library imported after table extraction
- **WHEN** the heading tree is extracted in a separate process after the full-text channel has already run
- **THEN** the full-text channel's table rows SHALL still contain correctly aligned values (e.g. `63,134,713`, not `63 134 713`), with no regression from the heading library's global side effects

### Requirement: Header/footer margin filtering
The full-text channel SHALL filter page headers/footers using separate top and bottom margins (`HEADER_MARGIN` / `FOOTER_MARGIN`), so that page-top content headings are retained while page-number footers are dropped.

#### Scenario: Page-top heading retained
- **WHEN** a section heading sits near the page top (y within the old combined-margin band, e.g. y≈52-66) and is not a repeated header or page number
- **THEN** the heading SHALL be retained in the full text so it can be located by the heading matcher

#### Scenario: Footer page number dropped
- **WHEN** a footer line contains a page number (e.g. `第 N/M 页` or `N / M 公司名`)
- **THEN** it SHALL be excluded from the full text

### Requirement: Heading tree from independent channel
The PDF heading tree SHALL be extracted from the pymupdf4llm Markdown channel (independent of the full-text channel) and cleaned of Markdown emphasis/HTML residuals before use. The cleaned heading tree SHALL populate `ParseResult.heading_tree` for entity extraction and heading-path lookup.

#### Scenario: Emphasis residuals stripped from headings
- **WHEN** pymupdf4llm emits a heading containing `**_AI_**` or `**X**` emphasis markers or `<mark>`/`<u>`/`<br>` tags
- **THEN** the heading stored in the tree SHALL contain only the plain text (e.g. `持续加大投资 AI 驱动增长`)

#### Scenario: Pseudo headings filtered
- **WHEN** a heading-like line matches pseudo-heading patterns (checkbox lines `√适用□不适用`, `编制单位：` annotations, footer page numbers)
- **THEN** it SHALL be excluded from the heading tree

### Requirement: Whitespace-normalized heading matching
Locating a heading in the full text SHALL compare after removing all whitespace, so that formatting differences between the heading channel and the full-text channel do not break matching. Unmatched headings SHALL be skipped silently, matching the existing fallback behavior.

#### Scenario: Heading located despite single-space difference
- **WHEN** the heading text has no spaces (e.g. `收入高质量增长运营效率持续提升`) but the full-text line has a single space (`收入高质量增长 运营效率持续提升`)
- **THEN** the heading line SHALL be located via whitespace-stripped comparison

#### Scenario: Heading located despite emphasis-adjacent punctuation
- **WHEN** the cleaned heading contains a space before punctuation (e.g. `约 1 , 120 亿港元` or `同比 8% ，毛利`) but the full-text line has it joined (`约 1,120 亿港元` / `同比 8%，毛利`)
- **THEN** the heading line SHALL be located via whitespace-stripped comparison

#### Scenario: Unmatched heading is skipped
- **WHEN** a heading cannot be matched in the full text even after whitespace stripping (e.g. a heading that is a table row wrapped in `|...|`, or text absent from the full text)
- **THEN** the heading SHALL be omitted from the segment table without raising an error, and remaining headings SHALL still be processed

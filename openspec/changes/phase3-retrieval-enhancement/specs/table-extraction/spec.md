# table-extraction Specification

## ADDED Requirements

### Requirement: Extract tables from PDF
The PyMuPDF Parser SHALL detect tables in PDF pages using `find_tables()` and convert each detected table into a complete Markdown table format, including a header row and separator row (`|---|---|`).

The extracted Markdown table SHALL be appended to the page text content after the extracted paragraphs.

#### Scenario: PDF with table extracts correctly
- **WHEN** a PDF page contains a table detectable by PyMuPDF
- **THEN** the parser SHALL produce a Markdown table with header, separator, and data rows appended to the page text

#### Scenario: PDF page without tables returns empty
- **WHEN** a PDF page has no detectable tables
- **THEN** the parser SHALL return no table output for that page

### Requirement: Extract tables from DOCX
The DOCX Parser SHALL iterate over all `python-docx` Table objects in the document and convert each to a Markdown table format, including a header row and separator row.

The extracted tables SHALL be appended after all paragraph text, separated by double newlines.

#### Scenario: DOCX with table extracts correctly
- **WHEN** a DOCX file contains tables
- **THEN** the parser SHALL produce Markdown tables for each detected table

#### Scenario: Empty table returns empty string
- **WHEN** a DOCX table has no rows
- **THEN** the parser SHALL return an empty string for that table

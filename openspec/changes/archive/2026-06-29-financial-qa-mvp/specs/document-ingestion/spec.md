## ADDED Requirements

### Requirement: PDF document parsing
The system SHALL parse PDF documents using PyMuPDF (fitz) to extract text content page by page.
- SHALL extract text content per page via `page.get_text()`
- SHALL extract embedded tables via `page.find_tables()` and append as Markdown text
- SHALL detect scanned PDFs (text extraction rate < 200 chars/page) and reject with error message
- SHALL encrypt/corrupted PDFs and reject with error message

#### Scenario: Parse a normal text-based PDF
- **WHEN** user uploads a text-based PDF file
- **THEN** system extracts text content per page with page numbers in metadata

#### Scenario: Reject scanned/image-only PDF
- **WHEN** user uploads a scanned PDF with less than 200 chars per page
- **THEN** system returns error message: "该PDF为扫描件，暂不支持"

#### Scenario: Reject encrypted PDF
- **WHEN** user uploads a password-protected PDF
- **THEN** system returns error message: "文件无法解析，请检查文件是否完整"

### Requirement: DOCX document parsing
The system SHALL parse DOCX documents using python-docx to extract text content paragraph by paragraph.

#### Scenario: Parse a standard DOCX file
- **WHEN** user uploads a .docx file
- **THEN** system extracts paragraph text with metadata (source filename, paragraph index)

### Requirement: TXT document parsing with encoding detection
The system SHALL parse TXT documents with automatic encoding detection.
- SHALL detect encoding using chardet library
- SHALL fallback to UTF-8 then GBK if detection fails
- SHALL return error if all encoding attempts fail

#### Scenario: Parse UTF-8 TXT file
- **WHEN** user uploads a UTF-8 encoded .txt file
- **THEN** system reads content correctly with UTF-8 encoding

#### Scenario: Parse GBK encoded TXT file
- **WHEN** user uploads a GBK encoded Chinese .txt file
- **THEN** system detects GBK encoding and reads content correctly

### Requirement: Document chunking
The system SHALL split parsed documents into chunks using RecursiveCharacterTextSplitter.
- SHALL support configurable chunk_size (default 512) and chunk_overlap (default 64)
- SHALL use Chinese-aware separators ["\n\n", "\n", "。", "；", " ", ""]
- SHALL perform chunk quality checks: count, avg length, length distribution (P10/P50/P90)
- SHALL detect and flag table fragments in chunk boundaries

#### Scenario: Chunk a parsed document
- **WHEN** a document has been parsed into raw text
- **THEN** system splits text into overlapping chunks with metadata (source, page, chunk_index, chunk_total)

### Requirement: File format validation
The system SHALL validate uploaded files before processing.
- SHALL accept only .pdf, .docx, .txt extensions
- SHALL enforce maximum file size limit (configurable, default 50MB)

#### Scenario: Reject unsupported file format
- **WHEN** user uploads a .xlsx or other unsupported file type
- **THEN** system rejects with message: "不支持的文件格式，请上传 PDF、DOCX 或 TXT 文件"

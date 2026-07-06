## ADDED Requirements

### Requirement: Gradio web interface
The system SHALL provide a Gradio 5.x web interface with Blocks layout.
- SHALL display a two-column layout: left for document management, right for conversation
- SHALL include knowledge base dropdown (with create/select/delete)
- SHALL include file upload button (PDF/DOCX/TXT)
- SHALL include chat input, chatbot display, and clear button
- SHALL stream assistant output with typing effect

#### Scenario: Upload file via interface
- **WHEN** user selects a knowledge base and uploads a file
- **THEN** system processes file asynchronously, shows progress indicator, and displays success/failure message

#### Scenario: Ask question and receive answer
- **WHEN** user types a question and submits
- **THEN** system streams answer token-by-token in the chatbot, followed by citation sources

### Requirement: Empty state guidance
The system SHALL guide first-time users when no knowledge base or documents exist.
- SHALL display welcome message explaining how to use the system
- SHALL show "知识库为空，请先上传文档" prompt when trying to query without documents

#### Scenario: First visit empty state
- **WHEN** user opens the application for the first time with no knowledge bases
- **THEN** system displays welcome message with step-by-step guidance

#### Scenario: Query without documents
- **WHEN** user asks a question but no documents have been uploaded
- **THEN** system responds "知识库为空，请先上传文档"

### Requirement: Asynchronous upload with progress
The system SHALL process file uploads asynchronously with progress feedback.
- SHALL use background thread for document parsing, chunking, embedding, and storage
- SHALL provide progress status (processing step + percentage) in the UI
- SHALL prevent user from sending queries while upload is in progress

#### Scenario: Upload large document
- **WHEN** user uploads a large document (>10MB)
- **THEN** system shows progress indicators: "解析中...", "向量化中...", "入库完成"

### Requirement: Knowledge base management
The system SHALL support knowledge base create, select, and delete operations.
- SHALL refresh dropdown after create/delete operations
- SHALL clear conversation history when switching knowledge bases
- SHALL show current knowledge base context in the chat area

#### Scenario: Switch knowledge base
- **WHEN** user selects a different knowledge base from the dropdown
- **THEN** conversation history is cleared and interface indicates the active knowledge base

#### Scenario: Delete knowledge base
- **WHEN** user deletes a knowledge base
- **THEN** MySQL metadata and ChromaDB collection are both removed, UI refreshes

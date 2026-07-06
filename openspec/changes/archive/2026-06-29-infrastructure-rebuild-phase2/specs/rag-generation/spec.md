## MODIFIED Requirements

### Requirement: LangChain 1.x upgrade
The RAG generation pipeline SHALL use LangChain 1.x packages.
- SHALL upgrade langchain-core from 0.3.x to 1.x
- SHALL upgrade langchain-openai from 0.3.x to 1.x
- SHALL upgrade langchain-text-splitters from 0.3.x to 1.x
- SHALL retain langchain-community at latest 0.x (no 1.x version available)
- SHALL add langchain-dashscope >= 0.1.0 for DashScope model integration
- SHALL use langchain_dashscope.DashScopeEmbeddings instead of community version
- SHALL use langchain_dashscope.DashScopeRerank instead of community version

#### Scenario: Model initialization with new packages
- **WHEN** the application starts
- **THEN** DashScopeEmbeddings initializes from langchain_dashscope
- **THEN** DashScopeRerank initializes from langchain_dashscope
- **THEN** ChatOpenAI continues to use langchain_openai (unchanged)

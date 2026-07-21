## MODIFIED Requirements

### Requirement: Docker Compose with additional services
The system docker-compose.yml SHALL include additional services for the new architecture.
- SHALL add postgres:15-alpine service for Langfuse database
- SHALL add langfuse/langfuse:2 service for LLM tracing UI
- SHALL add nginx service built from ./nginx/Dockerfile
- SHALL adjust app service: port 8000, command uses uvicorn
- SHALL add new named volumes: postgres_data

#### Scenario: All services start
- **WHEN** user runs `docker compose up -d --build`
- **THEN** all services start in dependency order (postgres → langfuse, mysql/redis → app → nginx)

### Requirement: Gradio UI retired
The Gradio-based UI at src/app.py SHALL be archived to old/ directory.
- SHALL copy src/app.py to old/src/app.py
- SHALL leave app.py in src/ for reference until frontend is ready
- SHALL set app container command to uvicorn instead of python -m src.app

### Requirement: .env template updated
The .env template SHALL include new configuration variables.
- SHALL include LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST, LANGFUSE_ENABLE
- SHALL include LANGFUSE_POSTGRES_PASS, NEXTAUTH_SECRET, LANGFUSE_SALT

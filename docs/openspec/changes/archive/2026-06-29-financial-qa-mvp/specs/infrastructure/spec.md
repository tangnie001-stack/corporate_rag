## ADDED Requirements

### Requirement: Docker Compose multi-service orchestration
The system SHALL deploy via docker-compose.yml with three services: app, mysql, redis.
- SHALL use mysql:8.0 image with healthcheck
- SHALL use redis:7-alpine image with healthcheck
- SHALL use custom app image built from Dockerfile
- SHALL use named volumes for data persistence (mysql_data, redis_data, chroma_data)
- SHALL create a shared docker network (app-network) for inter-service communication
- SHALL support env_file for API key injection

#### Scenario: Start all services
- **WHEN** user runs `docker-compose up --build`
- **THEN** all three services start in order (mysql → redis → app) with healthchecks

#### Scenario: Data survives restart
- **WHEN** user stops and restarts docker-compose
- **THEN** uploaded documents, vectors, and conversation history persist

### Requirement: MySQL schema initialization
The system SHALL auto-initialize MySQL tables on first startup.
- SHALL mount init SQL DDL script to MySQL container entrypoint
- SHALL create tables: knowledge_base, document, conversation_history

#### Scenario: Initialize database
- **WHEN** MySQL container starts for the first time
- **THEN** DDL script executes and creates all required tables

### Requirement: Configuration via environment variables
The system SHALL read all configuration from environment variables.
- SHALL provide .env.template with all required variables documented
- SHALL support Docker environment variables via docker-compose env_file
- SHALL fail with clear error message on missing required variables

#### Scenario: Missing required config
- **WHEN** DASHSCOPE_API_KEY is not set
- **THEN** system prints error on startup and shows message in Gradio UI

### Requirement: Graceful startup with retry
The system SHALL retry MySQL and Redis connections on startup.
- SHALL retry MySQL connection up to 5 times (2s interval, 2x backoff)
- SHALL retry Redis connection up to 3 times (1s interval, 2x backoff)
- SHALL log each retry attempt with elapsed time

#### Scenario: MySQL not ready on startup
- **WHEN** app starts before MySQL is healthy
- **THEN** app retries connection with exponential backoff and succeeds once MySQL is ready

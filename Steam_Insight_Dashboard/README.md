# Steam Insight Dashboard

A dashboard application for Steam Insight, containing both frontend (Next.js) and backend (Express/NestJS) services.

## Project Structure

- `app/web-html/` - Frontend HTML/CSS/JS application
- `app/was/` - Backend API server

## Getting Started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine with Docker Compose installed.

### Running Local Development Environment

To start the development environment using Docker Compose, run the following command in the root directory where `docker-compose.dev.yml` is located:

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

This will build and start both the `frontend` and `backend` services.

- **Frontend (Web):** Accessible at [http://localhost:3000](http://localhost:3000)
- **Backend (API):** Accessible at [http://localhost:4000](http://localhost:4000)

### Stopping the Environment

To stop the running containers, execute:

```bash
docker compose -f docker-compose.dev.yml down
```

## Environment Variables

Make sure to configure the `.env` file at the root of the project.
You may need to set specific API keys like `STEAM_API_KEY`.

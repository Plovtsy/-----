FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY static /static
RUN npm run build

FROM python:3.12-slim AS backend
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --trusted-host opensource.tbank.ru
COPY alembic.ini ./
COPY kontur ./kontur
COPY scripts ./scripts
COPY migrations ./migrations
COPY static ./static
COPY --from=frontend /frontend/dist ./frontend/dist
COPY data/example-history.csv ./data/example-history.csv
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python","-m","kontur.api"]

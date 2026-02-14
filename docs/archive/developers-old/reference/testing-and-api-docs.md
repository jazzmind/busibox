---
title: "Testing and API Documentation"
category: "developer"
order: 123
description: "Automated testing infrastructure and API docs for ingestion service"
published: true
---

# Testing and API Documentation

## Overview

This document describes the automated testing infrastructure and API documentation system for the Busibox ingestion service.

## Automated Worker Management in Tests

### Worker Fixture

Tests now automatically start and stop the ingestion worker process. No manual worker management required!

**Location**: `tests/integration/conftest.py`

**How it works**:
1. The `worker_process` fixture starts the worker before any tests run
2. Worker runs for the entire test session
3. Worker is gracefully stopped after all tests complete
4. If worker fails to start, tests are skipped with clear error message

**Usage**:
```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline_with_search(
    config: Config,
    test_user_id: str,
    client: TestClient,
    test_document_content: bytes,
    worker_process,  # Automatically starts worker
):
    # Test code here - worker is running
    pass
```

**Benefits**:
- ✅ No manual worker startup required
- ✅ Consistent test environment
- ✅ Automatic cleanup
- ✅ Clear error messages if worker fails
- ✅ Worker PID logged for debugging

### Running Tests

```bash
# Run all pipeline tests (worker starts automatically)
cd srv/ingest
pytest tests/integration/test_full_pipeline.py -v

# Run specific test
pytest tests/integration/test_full_pipeline.py::test_full_pipeline_with_search -v -s

# Run with worker output visible
pytest tests/integration/test_full_pipeline.py -v -s --capture=no
```

### Test Output

```
================================================================================
Starting ingestion worker for tests...
================================================================================
Worker started (PID: 12345)
================================================================================

tests/integration/test_full_pipeline.py::test_stage_1_upload PASSED
tests/integration/test_full_pipeline.py::test_stage_2_parsing PASSED
...

================================================================================
Stopping worker (PID: 12345)...
================================================================================
Worker stopped gracefully
================================================================================
```

## API Documentation

### OpenAPI/Swagger UI

The ingestion API now has comprehensive interactive documentation accessible via web browser.

**Access Points**:
- **Swagger UI** (interactive): `http://<ingest-lxc-ip>/docs`
- **ReDoc** (alternative): `http://<ingest-lxc-ip>/redoc`
- **OpenAPI JSON**: `http://<ingest-lxc-ip>/openapi.json`
- **Root**: `http://<ingest-lxc-ip>/` (redirects to /docs)

### Features

#### Swagger UI (`/docs`)
- Interactive API explorer
- Try out endpoints directly in browser
- See request/response schemas
- View example requests and responses
- Test authentication headers
- Download OpenAPI spec

#### ReDoc (`/redoc`)
- Clean, readable documentation
- Three-panel layout
- Search functionality
- Code samples in multiple languages
- Responsive design

#### OpenAPI JSON (`/openapi.json`)
- Machine-readable API specification
- Use with code generators
- Import into Postman/Insomnia
- Generate client SDKs

### Documentation Content

The API documentation includes:

1. **Overview**
   - Service description
   - Pipeline stages
   - Features list
   - Authentication requirements

2. **Endpoint Groups**
   - **Upload**: File upload with metadata
   - **Status**: Real-time processing status
   - **Files**: Metadata retrieval and deletion
   - **Health**: Service health checks

3. **Schemas**
   - Request/response models
   - Field descriptions
   - Validation rules
   - Example values

4. **Authentication**
   - Required headers (`X-User-Id`)
   - Access control
   - Rate limits

### Nginx Configuration

**Port**: 80 (HTTP)
**Configuration**: `/etc/nginx/sites-available/ingest-api`

**Features**:
- Root path redirects to `/docs`
- Proxies all requests to FastAPI (port 8000)
- Supports Server-Sent Events (SSE) for status updates
- Handles large file uploads (100MB max)
- Caches OpenAPI JSON schema (1 hour)
- Health check endpoint (no logging)

**Upstream**:
```nginx
upstream ingest_api {
    server 127.0.0.1:8000;
    keepalive 32;
}
```

## Deployment

### Deploy API with Documentation

```bash
cd provision/ansible

# Deploy to test environment
ansible-playbook -i inventory/test/hosts.yml site.yml --tags ingest_api

# Deploy to production
ansible-playbook -i inventory/production/hosts.yml site.yml --tags ingest_api
```

### What Gets Deployed

1. **FastAPI Application**
   - Python dependencies
   - API source code
   - Environment configuration
   - Systemd service

2. **Nginx**
   - Nginx installation
   - Site configuration
   - Enabled and started
   - Default site removed

3. **Services**
   - `ingest-api.service` (FastAPI on port 8000)
   - `nginx.service` (HTTP on port 80)

### Verify Deployment

```bash
# Check services are running
ssh root@<ingest-lxc-ip>
systemctl status ingest-api
systemctl status nginx

# Test API endpoint
curl http://localhost:8000/health

# Test nginx proxy
curl http://localhost/health

# Check documentation is accessible
curl -I http://localhost/docs
```

## Testing the API

### Using Swagger UI

1. Open browser to `http://<ingest-lxc-ip>/docs`
2. Expand an endpoint (e.g., `POST /upload`)
3. Click "Try it out"
4. Fill in parameters:
   - Headers: `X-User-Id: test-user-123`
   - Body: Upload a file
5. Click "Execute"
6. View response

### Using curl

```bash
# Health check
curl http://<ingest-lxc-ip>/health

# Upload file
curl -X POST http://<ingest-lxc-ip>/upload \
  -H "X-User-Id: test-user-123" \
  -F "file=@test.txt" \
  -F 'metadata={"tags":["test"]}'

# Get file status
curl http://<ingest-lxc-ip>/files/<file-id>

# Stream status updates (SSE)
curl -N http://<ingest-lxc-ip>/status/<file-id>
```

### Using Python

```python
import requests

# Upload file
with open("test.txt", "rb") as f:
    response = requests.post(
        "http://<ingest-lxc-ip>/upload",
        headers={"X-User-Id": "test-user-123"},
        files={"file": f},
        data={"metadata": '{"tags":["test"]}'}
    )

file_id = response.json()["fileId"]

# Get status
status = requests.get(f"http://<ingest-lxc-ip>/files/{file_id}")
print(status.json())
```

## Troubleshooting

### Worker Not Starting in Tests

**Symptom**: Tests fail with "Worker failed to start"

**Solutions**:
```bash
# Check worker script exists
ls -la srv/ingest/src/worker.py

# Check dependencies installed
cd srv/ingest
pip install -r requirements.txt

# Check services are running
# PostgreSQL, Redis, MinIO, Milvus, liteLLM must be accessible
```

### API Documentation Not Accessible

**Symptom**: 404 or connection refused on port 80

**Solutions**:
```bash
# Check nginx is running
systemctl status nginx

# Check nginx configuration
nginx -t

# Check nginx logs
tail -f /var/log/nginx/ingest-api-error.log

# Check FastAPI is running
systemctl status ingest-api
curl http://localhost:8000/docs
```

### Nginx 502 Bad Gateway

**Symptom**: Nginx returns 502 when accessing /docs

**Solutions**:
```bash
# Check FastAPI is running
systemctl status ingest-api

# Check FastAPI logs
journalctl -u ingest-api -n 50

# Check port 8000 is listening
netstat -tlnp | grep 8000

# Restart services
systemctl restart ingest-api
systemctl restart nginx
```

### SSE Not Working

**Symptom**: Status updates not streaming

**Solutions**:
```bash
# Check nginx buffering is disabled
grep proxy_buffering /etc/nginx/sites-available/ingest-api

# Should see: proxy_buffering off;

# Test SSE directly to FastAPI
curl -N http://localhost:8000/status/<file-id>

# If that works, nginx config needs update
```

## Performance

### Expected Response Times

- **Health check**: < 10ms
- **File upload** (1MB): 100-500ms
- **Status query**: < 50ms
- **SSE connection**: < 100ms to establish
- **Documentation pages**: < 100ms (cached)

### Concurrent Connections

- **Nginx**: 1024 connections (default)
- **FastAPI**: 10 workers (configurable)
- **Upload limit**: 10 concurrent per user
- **SSE connections**: 100 concurrent

### Resource Usage

- **Nginx**: ~10MB RAM
- **FastAPI**: ~100MB RAM per worker
- **Worker**: ~200MB RAM (varies with document size)

## Security

### Network Access

- **Internal only**: No external access
- **Port 80**: HTTP (internal network)
- **Authentication**: Required for all endpoints (X-User-Id header)
- **Rate limiting**: Recommended (not yet implemented)

### File Upload Security

- **Max size**: 100MB
- **Timeout**: 300 seconds
- **Validation**: MIME type checking
- **Storage**: Isolated per user in MinIO

### Documentation Access

- **Public within network**: Anyone on internal network can view docs
- **No sensitive data**: Docs don't expose credentials or internal IPs
- **Read-only**: Documentation is view-only, can't modify API

## Future Enhancements

### Testing
- [ ] Add performance benchmarks to tests
- [ ] Test concurrent uploads
- [ ] Test error recovery scenarios
- [ ] Add load testing suite

### API Documentation
- [ ] Add authentication examples
- [ ] Include code samples for common languages
- [ ] Add troubleshooting guide to docs
- [ ] Create Postman collection

### Deployment
- [ ] Add HTTPS support
- [ ] Configure rate limiting
- [ ] Add API versioning
- [ ] Implement request logging


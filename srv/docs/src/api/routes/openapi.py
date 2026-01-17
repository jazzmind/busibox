"""
OpenAPI specification endpoints.

Serves OpenAPI YAML specifications for busibox services.
"""

from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.services.docs_loader import get_docs_loader

router = APIRouter()


class OpenAPISpecInfo(BaseModel):
    """OpenAPI specification summary."""
    service: str
    title: str
    version: str
    description: str


class OpenAPIListResponse(BaseModel):
    """List of available OpenAPI specifications."""
    specs: list[OpenAPISpecInfo]


@router.get("", response_model=OpenAPIListResponse)
async def list_openapi_specs():
    """
    List all available OpenAPI specifications.
    
    Returns metadata about each available specification without
    the full YAML content.
    """
    loader = get_docs_loader()
    specs = loader.list_openapi_specs()
    
    return OpenAPIListResponse(
        specs=[
            OpenAPISpecInfo(
                service=spec.service,
                title=spec.title,
                version=spec.version,
                description=spec.description[:200] + "..." if len(spec.description) > 200 else spec.description,
            )
            for spec in specs
        ]
    )


@router.get("/{service}", response_class=PlainTextResponse)
async def get_openapi_spec(service: Literal["agent", "authz", "ingest", "search", "docs"]):
    """
    Get an OpenAPI specification by service name.
    
    Returns the raw YAML content of the specification file.
    """
    loader = get_docs_loader()
    content = loader.get_openapi_spec(service)
    
    if content is None:
        raise HTTPException(status_code=404, detail=f"OpenAPI specification not found: {service}")
    
    return PlainTextResponse(content=content, media_type="application/yaml")

"""
Documentation endpoints.

Serves markdown documentation files with frontmatter parsing.
"""

from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.docs_loader import get_docs_loader

router = APIRouter()


class DocNavItemResponse(BaseModel):
    """Navigation item for sidebar."""
    slug: str
    title: str
    description: str
    order: int


class DocFrontmatterResponse(BaseModel):
    """Frontmatter metadata for a document."""
    title: str
    category: str
    order: int
    description: str
    published: bool


class DocResponse(BaseModel):
    """Full document response."""
    slug: str
    frontmatter: DocFrontmatterResponse
    content: str


class DocNavigationResponse(BaseModel):
    """Previous/next navigation for a document."""
    prev: dict | None
    next: dict | None


class DocListResponse(BaseModel):
    """List of documents for a category."""
    category: str
    docs: list[DocNavItemResponse]


@router.get("/{category}", response_model=DocListResponse)
async def list_docs(category: Literal["user", "developer"]):
    """
    List all published documentation for a category.
    
    Returns navigation items (slug, title, description, order) for building
    a documentation sidebar or index page.
    """
    loader = get_docs_loader()
    nav_items = loader.get_docs_navigation(category)
    
    return DocListResponse(
        category=category,
        docs=[
            DocNavItemResponse(
                slug=item.slug,
                title=item.title,
                description=item.description,
                order=item.order,
            )
            for item in nav_items
        ]
    )


@router.get("/{category}/{slug}", response_model=DocResponse)
async def get_doc(category: Literal["user", "developer"], slug: str):
    """
    Get a single documentation file by category and slug.
    
    Returns the full document including frontmatter and markdown content.
    """
    loader = get_docs_loader()
    doc = loader.get_doc_by_slug(category, slug)
    
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {category}/{slug}")
    
    return DocResponse(
        slug=doc.slug,
        frontmatter=DocFrontmatterResponse(
            title=doc.frontmatter.title,
            category=doc.frontmatter.category,
            order=doc.frontmatter.order,
            description=doc.frontmatter.description,
            published=doc.frontmatter.published,
        ),
        content=doc.content,
    )


@router.get("/{category}/{slug}/navigation", response_model=DocNavigationResponse)
async def get_doc_navigation(category: Literal["user", "developer"], slug: str):
    """
    Get previous and next documents for navigation.
    
    Useful for building "Previous" / "Next" navigation links at the
    bottom of documentation pages.
    """
    loader = get_docs_loader()
    
    # Verify the document exists
    doc = loader.get_doc_by_slug(category, slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {category}/{slug}")
    
    nav = loader.get_doc_navigation(category, slug)
    return DocNavigationResponse(prev=nav['prev'], next=nav['next'])

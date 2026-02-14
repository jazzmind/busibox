"""
Documentation endpoints.

Serves markdown documentation files with frontmatter parsing.

Categories:
  - platform: End-user guides (previously 'user', still accepted for backward compat)
  - administrator: Deployment, configuration, and operational guides
  - apps: Per-app documentation contributed by installed applications
  - developer: Technical/developer documentation
"""

from typing import Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.docs_loader import get_docs_loader, normalize_category

router = APIRouter()

# Accepted category values (includes 'user' alias for backward compatibility)
CategoryType = Literal["platform", "administrator", "apps", "developer", "user"]


class DocNavItemResponse(BaseModel):
    """Navigation item for sidebar."""
    slug: str
    title: str
    description: str
    order: int
    app_id: Optional[str] = None
    app_name: Optional[str] = None


class DocFrontmatterResponse(BaseModel):
    """Frontmatter metadata for a document."""
    title: str
    category: str
    order: int
    description: str
    published: bool
    app_id: Optional[str] = None
    app_name: Optional[str] = None


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


class AppDocsGroupResponse(BaseModel):
    """A group of docs belonging to a single app."""
    app_id: str
    app_name: str
    docs: list[DocNavItemResponse]


class AppDocsGroupsResponse(BaseModel):
    """All app doc groups."""
    groups: list[AppDocsGroupResponse]


@router.get("/apps/groups", response_model=AppDocsGroupsResponse)
async def list_apps_docs_groups():
    """
    List all app documentation grouped by app_id.
    
    Returns docs organized by application, useful for building an
    apps documentation sidebar with app headings.
    """
    loader = get_docs_loader()
    groups = loader.get_apps_docs_groups()
    
    return AppDocsGroupsResponse(
        groups=[
            AppDocsGroupResponse(
                app_id=group.app_id,
                app_name=group.app_name,
                docs=[
                    DocNavItemResponse(
                        slug=item.slug,
                        title=item.title,
                        description=item.description,
                        order=item.order,
                        app_id=item.app_id,
                        app_name=item.app_name,
                    )
                    for item in group.docs
                ]
            )
            for group in groups
        ]
    )


@router.get("/{category}", response_model=DocListResponse)
async def list_docs(category: CategoryType):
    """
    List all published documentation for a category.
    
    Returns navigation items (slug, title, description, order) for building
    a documentation sidebar or index page.
    
    Accepts 'user' as alias for 'platform' for backward compatibility.
    """
    resolved_category = normalize_category(category)
    loader = get_docs_loader()
    nav_items = loader.get_docs_navigation(resolved_category)
    
    return DocListResponse(
        category=resolved_category,
        docs=[
            DocNavItemResponse(
                slug=item.slug,
                title=item.title,
                description=item.description,
                order=item.order,
                app_id=item.app_id,
                app_name=item.app_name,
            )
            for item in nav_items
        ]
    )


@router.get("/{category}/{slug}", response_model=DocResponse)
async def get_doc(category: CategoryType, slug: str):
    """
    Get a single documentation file by category and slug.
    
    Returns the full document including frontmatter and markdown content.
    Accepts 'user' as alias for 'platform' for backward compatibility.
    """
    resolved_category = normalize_category(category)
    loader = get_docs_loader()
    doc = loader.get_doc_by_slug(resolved_category, slug)
    
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {resolved_category}/{slug}")
    
    return DocResponse(
        slug=doc.slug,
        frontmatter=DocFrontmatterResponse(
            title=doc.frontmatter.title,
            category=doc.frontmatter.category,
            order=doc.frontmatter.order,
            description=doc.frontmatter.description,
            published=doc.frontmatter.published,
            app_id=doc.frontmatter.app_id,
            app_name=doc.frontmatter.app_name,
        ),
        content=doc.content,
    )


@router.get("/{category}/{slug}/navigation", response_model=DocNavigationResponse)
async def get_doc_navigation(category: CategoryType, slug: str):
    """
    Get previous and next documents for navigation.
    
    Useful for building "Previous" / "Next" navigation links at the
    bottom of documentation pages.
    Accepts 'user' as alias for 'platform' for backward compatibility.
    """
    resolved_category = normalize_category(category)
    loader = get_docs_loader()
    
    # Verify the document exists
    doc = loader.get_doc_by_slug(resolved_category, slug)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {resolved_category}/{slug}")
    
    nav = loader.get_doc_navigation(resolved_category, slug)
    return DocNavigationResponse(prev=nav['prev'], next=nav['next'])

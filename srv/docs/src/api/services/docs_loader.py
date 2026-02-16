"""
Documentation file loader service.

Handles reading and parsing markdown documentation files with frontmatter,
as well as OpenAPI specification files.

Supported categories:
  - platform: End-user guides for using Busibox (previously 'user')
  - administrator: Deployment, configuration, and operational guides
  - apps: Per-app documentation contributed by installed applications
  - developer: Technical/developer documentation

For backward compatibility, 'user' is accepted as an alias for 'platform'.

App docs use additional frontmatter fields:
  - app_id: Identifier for the app (e.g. 'busibox-agents')
  - app_name: Human-readable app name (e.g. 'Agent Manager')
"""

import os
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import yaml

# Valid categories for documentation
VALID_CATEGORIES = ('platform', 'administrator', 'apps', 'developer')

# Backward compatibility: 'user' maps to 'platform'
CATEGORY_ALIASES = {'user': 'platform'}


def normalize_category(category: str) -> str:
    """Normalize category name, applying aliases."""
    return CATEGORY_ALIASES.get(category, category)


@dataclass
class DocFrontmatter:
    """Frontmatter schema for documentation files."""
    title: str
    category: str  # 'platform', 'apps', or 'developer'
    order: int
    description: str
    published: bool
    app_id: Optional[str] = None
    app_name: Optional[str] = None


@dataclass
class DocFile:
    """Parsed documentation file."""
    slug: str
    frontmatter: DocFrontmatter
    content: str
    file_path: str


@dataclass
class DocNavItem:
    """Navigation item for sidebar."""
    slug: str
    title: str
    description: str
    order: int
    app_id: Optional[str] = None
    app_name: Optional[str] = None


@dataclass
class AppDocsGroup:
    """A group of docs belonging to a single app."""
    app_id: str
    app_name: str
    docs: list[DocNavItem] = field(default_factory=list)


@dataclass
class OpenAPISpec:
    """OpenAPI specification info."""
    service: str
    title: str
    version: str
    description: str


class DocsLoader:
    """Service for loading documentation and OpenAPI files."""
    
    def __init__(self, docs_path: str = "/app/docs", openapi_path: str = "/app/openapi"):
        self.docs_path = Path(docs_path)
        self.openapi_path = Path(openapi_path)
    
    def _parse_frontmatter(self, content: str) -> tuple[Optional[dict], str]:
        """Parse YAML frontmatter from markdown content."""
        # Match frontmatter between --- delimiters
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
        if not match:
            return None, content
        
        try:
            frontmatter = yaml.safe_load(match.group(1))
            body = match.group(2)
            return frontmatter, body
        except yaml.YAMLError:
            return None, content
    
    def _has_valid_frontmatter(self, data: Optional[dict]) -> bool:
        """Check if frontmatter has all required fields for publishing."""
        if data is None:
            return False
        
        raw_category = data.get('category', '')
        category = normalize_category(raw_category)
        
        base_valid = (
            isinstance(data.get('title'), str) and
            category in VALID_CATEGORIES and
            isinstance(data.get('order'), (int, float)) and
            isinstance(data.get('description'), str) and
            data.get('published') is True
        )
        
        if not base_valid:
            return False
        
        # Apps category requires app_id
        if category == 'apps':
            return isinstance(data.get('app_id'), str) and len(data['app_id']) > 0
        
        return True
    
    def _to_doc_frontmatter(self, data: dict) -> DocFrontmatter:
        """Convert raw dict to DocFrontmatter."""
        category = normalize_category(data['category'])
        return DocFrontmatter(
            title=data['title'],
            category=category,
            order=int(data['order']),
            description=data['description'],
            published=data['published'],
            app_id=data.get('app_id'),
            app_name=data.get('app_name'),
        )
    
    def _generate_slug(self, file_path: Path) -> str:
        """Generate a URL-friendly slug from file path."""
        relative = file_path.relative_to(self.docs_path)
        # Remove .md extension and convert path separators to dashes
        slug = str(relative).replace('.md', '').replace('/', '-').replace('\\', '-').lower()
        return slug
    
    def _find_markdown_files(self, directory: Path) -> list[Path]:
        """Recursively find all markdown files in a directory."""
        files = []
        
        if not directory.exists():
            return files
        
        for entry in directory.iterdir():
            if entry.is_dir():
                # Skip archive directories
                if entry.name == 'archive':
                    continue
                files.extend(self._find_markdown_files(entry))
            elif entry.is_file() and entry.suffix == '.md':
                files.append(entry)
        
        return files
    
    def get_docs_by_category(self, category: str) -> list[DocFile]:
        """Get all published documentation files for a category.
        
        Accepts 'user' as alias for 'platform' for backward compatibility.
        """
        category = normalize_category(category)
        if category not in VALID_CATEGORIES:
            return []
        
        files = self._find_markdown_files(self.docs_path)
        docs = []
        
        for file_path in files:
            try:
                content = file_path.read_text(encoding='utf-8')
                frontmatter_data, body = self._parse_frontmatter(content)
                
                if not self._has_valid_frontmatter(frontmatter_data):
                    continue
                
                doc_category = normalize_category(frontmatter_data.get('category', ''))
                if doc_category == category:
                    docs.append(DocFile(
                        slug=self._generate_slug(file_path),
                        frontmatter=self._to_doc_frontmatter(frontmatter_data),
                        content=body,
                        file_path=str(file_path),
                    ))
            except Exception as e:
                print(f"Error reading doc file {file_path}: {e}")
        
        # Sort by order
        docs.sort(key=lambda d: d.frontmatter.order)
        return docs
    
    def get_docs_navigation(self, category: str) -> list[DocNavItem]:
        """Get navigation items for a category (lighter weight than full docs)."""
        docs = self.get_docs_by_category(category)
        return [
            DocNavItem(
                slug=doc.slug,
                title=doc.frontmatter.title,
                description=doc.frontmatter.description,
                order=doc.frontmatter.order,
                app_id=doc.frontmatter.app_id,
                app_name=doc.frontmatter.app_name,
            )
            for doc in docs
        ]
    
    def get_apps_docs_groups(self) -> list[AppDocsGroup]:
        """Get app docs grouped by app_id.
        
        Returns a list of AppDocsGroup, each containing the docs for one app,
        sorted by app_name. Docs within each group are sorted by order.
        """
        docs = self.get_docs_by_category('apps')
        groups: dict[str, AppDocsGroup] = {}
        
        for doc in docs:
            app_id = doc.frontmatter.app_id
            if not app_id:
                continue
            
            if app_id not in groups:
                groups[app_id] = AppDocsGroup(
                    app_id=app_id,
                    app_name=doc.frontmatter.app_name or app_id.replace('-', ' ').title(),
                    docs=[],
                )
            
            groups[app_id].docs.append(DocNavItem(
                slug=doc.slug,
                title=doc.frontmatter.title,
                description=doc.frontmatter.description,
                order=doc.frontmatter.order,
                app_id=doc.frontmatter.app_id,
                app_name=doc.frontmatter.app_name,
            ))
        
        # Sort groups by app_name, docs within each group already sorted by order
        result = sorted(groups.values(), key=lambda g: g.app_name.lower())
        return result
    
    def get_doc_by_slug(self, category: str, slug: str) -> Optional[DocFile]:
        """Get a single documentation file by slug and category."""
        docs = self.get_docs_by_category(category)
        for doc in docs:
            if doc.slug == slug:
                return doc
        return None
    
    def get_doc_navigation(self, category: str, slug: str) -> dict:
        """Get previous and next docs for navigation."""
        nav = self.get_docs_navigation(category)
        current_index = -1
        
        for i, item in enumerate(nav):
            if item.slug == slug:
                current_index = i
                break
        
        prev_doc = nav[current_index - 1] if current_index > 0 else None
        next_doc = nav[current_index + 1] if current_index < len(nav) - 1 and current_index >= 0 else None
        
        return {
            'prev': {
                'slug': prev_doc.slug,
                'title': prev_doc.title,
            } if prev_doc else None,
            'next': {
                'slug': next_doc.slug,
                'title': next_doc.title,
            } if next_doc else None,
        }
    
    def list_openapi_specs(self) -> list[OpenAPISpec]:
        """List all available OpenAPI specifications."""
        specs = []
        
        if not self.openapi_path.exists():
            return specs
        
        for file_path in self.openapi_path.glob('*-api.yaml'):
            try:
                content = file_path.read_text(encoding='utf-8')
                data = yaml.safe_load(content)
                
                info = data.get('info', {})
                service = file_path.stem.replace('-api', '')
                
                specs.append(OpenAPISpec(
                    service=service,
                    title=info.get('title', service.title()),
                    version=info.get('version', '1.0.0'),
                    description=info.get('description', ''),
                ))
            except Exception as e:
                print(f"Error reading OpenAPI spec {file_path}: {e}")
        
        return specs
    
    def get_openapi_spec(self, service: str) -> Optional[str]:
        """Get an OpenAPI specification by service name."""
        file_path = self.openapi_path / f"{service}-api.yaml"
        
        if not file_path.exists():
            return None
        
        try:
            return file_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Error reading OpenAPI spec {file_path}: {e}")
            return None


# Singleton instance
_docs_loader: Optional[DocsLoader] = None


def get_docs_loader() -> DocsLoader:
    """Get the singleton DocsLoader instance."""
    global _docs_loader
    if _docs_loader is None:
        docs_path = os.environ.get('DOCS_PATH', '/app/docs')
        openapi_path = os.environ.get('OPENAPI_PATH', '/app/openapi')
        _docs_loader = DocsLoader(docs_path, openapi_path)
    return _docs_loader

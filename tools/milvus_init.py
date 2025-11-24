#!/usr/bin/env python3
"""
Milvus Vector Database Initialization Script

This script initializes the Milvus vector database for the busibox platform.
It creates the 'documents' collection with hybrid search support:
- text_dense: FastEmbed (bge-large-en-v1.5) 1024-d embeddings
- text_sparse: BM25 sparse vectors for keyword search
- page_vectors: ColPali 128-d pooled image embeddings

Usage:
    python tools/milvus_init.py [--drop]

Options:
    --drop    Drop existing collection before creating (data loss!)

Environment Variables:
    MILVUS_HOST: Milvus server host (default: 10.96.200.204)
    MILVUS_PORT: Milvus server port (default: 19530)
"""

import os
import sys
from pymilvus import (
    connections,
    utility,
    FieldSchema,
    CollectionSchema,
    DataType,
    Collection,
)


def get_config():
    """Get configuration from environment variables with defaults."""
    return {
        "host": os.getenv("MILVUS_HOST", "10.96.200.204"),
        "port": int(os.getenv("MILVUS_PORT", "19530")),
        "collection_name": os.getenv("MILVUS_COLLECTION", "documents"),
        "text_dim": 1024,  # FastEmbed bge-large-en-v1.5
        "image_dim": 128,  # ColPali pooled
    }


def connect_milvus(host, port):
    """Connect to Milvus server."""
    print(f"Connecting to Milvus at {host}:{port}...")
    try:
        connections.connect("default", host=host, port=port)
        print("✓ Connected successfully")
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


def drop_collection(collection_name):
    """Drop collection if it exists."""
    if utility.has_collection(collection_name):
        print(f"Dropping existing collection '{collection_name}'...")
        try:
            utility.drop_collection(collection_name)
            print(f"✓ Collection '{collection_name}' dropped")
            return True
        except Exception as e:
            print(f"✗ Failed to drop collection: {e}")
            return False
    else:
        print(f"Collection '{collection_name}' does not exist (nothing to drop)")
        return True


def create_collection(collection_name, text_dim, image_dim):
    """Create the documents collection with hybrid search schema."""
    
    # Check if collection already exists
    if utility.has_collection(collection_name):
        print(f"Collection '{collection_name}' already exists")
        collection = Collection(collection_name)
        print(f"  - Current entities: {collection.num_entities}")
        print(f"  - Schema: {len(collection.schema.fields)} fields")
        print("\nUse --drop flag to drop and recreate the collection")
        return collection
    
    print(f"Creating collection '{collection_name}' with hybrid search schema...")
    
    # Define schema fields
    fields = [
        # Primary key
        FieldSchema(
            name="id",
            dtype=DataType.VARCHAR,
            is_primary=True,
            max_length=256,
            description="Vector ID (file_id-chunk-N or file_id-page-N)"
        ),
        
        # File metadata
        FieldSchema(
            name="file_id",
            dtype=DataType.VARCHAR,
            max_length=36,
            description="File UUID for permission filtering"
        ),
        FieldSchema(
            name="chunk_index",
            dtype=DataType.INT64,
            description="Chunk index within file (-1 for page images)"
        ),
        FieldSchema(
            name="page_number",
            dtype=DataType.INT64,
            description="Page number (0 for non-PDF chunks)"
        ),
        FieldSchema(
            name="modality",
            dtype=DataType.VARCHAR,
            max_length=32,
            description="Modality: 'text' or 'page_image'"
        ),
        
        # Content
        FieldSchema(
            name="text",
            dtype=DataType.VARCHAR,
            max_length=65535,
            description="Chunk text content"
        ),
        
        # Vector embeddings
        FieldSchema(
            name="text_dense",
            dtype=DataType.FLOAT_VECTOR,
            dim=text_dim,
            description=f"Dense text embedding (FastEmbed bge-large-en-v1.5, {text_dim}-d)"
        ),
        FieldSchema(
            name="text_sparse",
            dtype=DataType.SPARSE_FLOAT_VECTOR,
            description="Sparse BM25 vector for keyword search"
        ),
        FieldSchema(
            name="page_vectors",
            dtype=DataType.FLOAT_VECTOR,
            dim=image_dim,
            description=f"Pooled ColPali image embedding ({image_dim}-d)"
        ),
        
        # User permissions
        FieldSchema(
            name="user_id",
            dtype=DataType.VARCHAR,
            max_length=36,
            description="User UUID for row-level security"
        ),
        
        # Metadata (JSON)
        FieldSchema(
            name="metadata",
            dtype=DataType.JSON,
            description="Additional metadata (content_hash, language, etc.)"
        ),
    ]
    
    # Create schema
    schema = CollectionSchema(
        fields=fields,
        description="Document chunks and page images with hybrid search support"
    )
    
    # Create collection
    collection = Collection(
        name=collection_name,
        schema=schema,
        using="default"
    )
    
    print(f"✓ Collection '{collection_name}' created")
    print(f"  - Text embedding dimension: {text_dim}")
    print(f"  - Image embedding dimension: {image_dim}")
    return collection


def create_indexes(collection):
    """Create vector indexes for efficient similarity search."""
    print("\nCreating indexes...")
    
    # Check if indexes already exist
    if collection.has_index():
        print("  Indexes already exist:")
        indexes = collection.indexes
        for index in indexes:
            print(f"    - Field: {index.field_name}, Type: {index.params.get('index_type', 'unknown')}")
        return
    
    # Index 1: Dense text embeddings (HNSW for high recall)
    print("  Creating HNSW index on text_dense...")
    text_index_params = {
        "index_type": "HNSW",
        "metric_type": "COSINE",  # Cosine similarity for semantic search
        "params": {
            "M": 16,  # Max degree of node
            "efConstruction": 256  # Search depth during construction
        }
    }
    collection.create_index(
        field_name="text_dense",
        index_params=text_index_params
    )
    print("    ✓ text_dense index created (HNSW, COSINE)")
    
    # Index 2: Sparse BM25 vectors (auto-indexed by Milvus)
    print("  Creating SPARSE_INVERTED_INDEX on text_sparse...")
    sparse_index_params = {
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "IP",  # Inner product for sparse vectors
    }
    collection.create_index(
        field_name="text_sparse",
        index_params=sparse_index_params
    )
    print("    ✓ text_sparse index created (SPARSE_INVERTED_INDEX, IP)")
    
    # Index 3: ColPali image embeddings (IVF_FLAT for speed/quality balance)
    print("  Creating IVF_FLAT index on page_vectors...")
    image_index_params = {
        "index_type": "IVF_FLAT",
        "metric_type": "COSINE",  # Cosine similarity for image search
        "params": {
            "nlist": 128  # Number of clusters
        }
    }
    collection.create_index(
        field_name="page_vectors",
        index_params=image_index_params
    )
    print("    ✓ page_vectors index created (IVF_FLAT, COSINE)")


def load_collection(collection):
    """Load collection into memory for querying."""
    print("\nLoading collection into memory...")
    collection.load()
    print("✓ Collection loaded and ready for queries")


def verify_setup(collection):
    """Verify the collection is properly set up."""
    print("\n" + "=" * 60)
    print("Verification")
    print("=" * 60)
    print(f"Collection name: {collection.name}")
    print(f"Schema fields: {len(collection.schema.fields)}")
    print(f"Current entities: {collection.num_entities}")
    
    # Display field details
    print("\nFields:")
    for field in collection.schema.fields:
        field_type = field.dtype.name
        extra = ""
        if field_type == "FLOAT_VECTOR":
            extra = f", dim={field.params.get('dim', 'N/A')}"
        elif field_type == "VARCHAR":
            extra = f", max_length={field.params.get('max_length', 'N/A')}"
        print(f"  - {field.name} ({field_type}{extra})")
    
    # Display indexes
    print("\nIndexes:")
    if collection.has_index():
        indexes = collection.indexes
        for index in indexes:
            metric = index.params.get('metric_type', 'N/A')
            idx_type = index.params.get('index_type', 'unknown')
            print(f"  - {index.field_name}: {idx_type} (metric: {metric})")
    else:
        print("  (none)")


def main():
    """Main execution function."""
    print("=" * 60)
    print("Busibox Milvus Initialization Script")
    print("Hybrid Search: FastEmbed + BM25 + ColPali")
    print("=" * 60)
    print()
    
    # Check for --drop flag
    drop_existing = "--drop" in sys.argv
    
    # Get configuration
    config = get_config()
    
    print("Configuration:")
    print(f"  Host: {config['host']}")
    print(f"  Port: {config['port']}")
    print(f"  Collection Name: {config['collection_name']}")
    print(f"  Text Embedding Dimension: {config['text_dim']} (FastEmbed bge-large-en-v1.5)")
    print(f"  Image Embedding Dimension: {config['image_dim']} (ColPali pooled)")
    print(f"  Drop Existing: {drop_existing}")
    print()
    
    # Connect to Milvus
    if not connect_milvus(config["host"], config["port"]):
        sys.exit(1)
    
    print()
    
    # Drop existing collection if requested
    if drop_existing:
        if not drop_collection(config["collection_name"]):
            sys.exit(1)
        print()
    
    # Create collection
    collection = create_collection(
        config["collection_name"],
        config["text_dim"],
        config["image_dim"]
    )
    
    # Only create indexes and load if this is a new collection
    if collection.num_entities == 0 and not collection.has_index():
        create_indexes(collection)
        load_collection(collection)
    
    # Verify setup
    verify_setup(collection)
    
    print()
    print("=" * 60)
    print("✓ Milvus initialization complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Update ingestion service configuration")
    print("  2. Deploy updated code to ingest workers")
    print("  3. Start ingesting documents")
    print()
    
    # Disconnect
    connections.disconnect("default")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

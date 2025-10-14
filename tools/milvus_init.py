#!/usr/bin/env python3
"""
Milvus Vector Database Initialization Script

This script initializes the Milvus vector database for the busibox platform.
It creates the document_embeddings collection with the proper schema and indexes.

Usage:
    python tools/milvus_init.py

Environment Variables:
    MILVUS_HOST: Milvus server host (default: 10.96.200.23)
    MILVUS_PORT: Milvus server port (default: 19530)
    EMBEDDING_DIM: Vector dimension for embeddings (default: 768)
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
        "host": os.getenv("MILVUS_HOST", "10.96.200.23"),
        "port": int(os.getenv("MILVUS_PORT", "19530")),
        "embedding_dim": int(os.getenv("EMBEDDING_DIM", "768")),
        "collection_name": "document_embeddings",
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


def create_collection(collection_name, embedding_dim):
    """Create the document_embeddings collection if it doesn't exist."""
    
    # Check if collection already exists
    if utility.has_collection(collection_name):
        print(f"Collection '{collection_name}' already exists")
        
        # Get existing collection and verify schema
        collection = Collection(collection_name)
        print(f"  - Current entities: {collection.num_entities}")
        print(f"  - Schema: {len(collection.schema.fields)} fields")
        
        return collection
    
    print(f"Creating collection '{collection_name}'...")
    
    # Define schema fields
    fields = [
        FieldSchema(
            name="id",
            dtype=DataType.VARCHAR,
            is_primary=True,
            max_length=36,
            description="Chunk UUID"
        ),
        FieldSchema(
            name="vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=embedding_dim,
            description="Embedding vector"
        ),
        FieldSchema(
            name="file_id",
            dtype=DataType.VARCHAR,
            max_length=36,
            description="File UUID for permission filtering"
        ),
        FieldSchema(
            name="chunk_id",
            dtype=DataType.VARCHAR,
            max_length=36,
            description="Chunk UUID (same as id for clarity)"
        ),
        FieldSchema(
            name="model_name",
            dtype=DataType.VARCHAR,
            max_length=100,
            description="Embedding model name"
        ),
        FieldSchema(
            name="created_at",
            dtype=DataType.INT64,
            description="Unix timestamp"
        ),
    ]
    
    # Create schema
    schema = CollectionSchema(
        fields=fields,
        description="Document chunk embeddings for semantic search"
    )
    
    # Create collection
    collection = Collection(
        name=collection_name,
        schema=schema,
        using="default"
    )
    
    print(f"✓ Collection '{collection_name}' created")
    return collection


def create_index(collection):
    """Create vector index for efficient similarity search."""
    print("Creating index on vector field...")
    
    # Check if index already exists
    if collection.has_index():
        print("  Index already exists")
        indexes = collection.indexes
        for index in indexes:
            print(f"  - Field: {index.field_name}, Type: {index.params.get('index_type', 'unknown')}")
        return
    
    # Define index parameters
    # HNSW (Hierarchical Navigable Small World) is recommended for high recall
    index_params = {
        "index_type": "HNSW",
        "metric_type": "L2",  # Euclidean distance (can also use "IP" for inner product)
        "params": {
            "M": 16,  # Max degree of node
            "efConstruction": 256  # Search depth during construction
        }
    }
    
    # Create index
    collection.create_index(
        field_name="vector",
        index_params=index_params
    )
    
    print(f"✓ Index created (type: HNSW, metric: L2)")


def load_collection(collection):
    """Load collection into memory for querying."""
    print("Loading collection into memory...")
    
    collection.load()
    
    print("✓ Collection loaded and ready for queries")


def verify_setup(collection):
    """Verify the collection is properly set up."""
    print("\nVerification:")
    print(f"  Collection name: {collection.name}")
    print(f"  Schema fields: {len(collection.schema.fields)}")
    print(f"  Current entities: {collection.num_entities}")
    print(f"  Loaded: {collection.is_loaded if hasattr(collection, 'is_loaded') else 'N/A'}")
    
    # Display field details
    print("\n  Fields:")
    for field in collection.schema.fields:
        field_type = field.dtype.name
        extra = f", dim={field.params.get('dim', 'N/A')}" if field_type == "FLOAT_VECTOR" else ""
        extra += f", max_length={field.params.get('max_length', 'N/A')}" if field.dtype == DataType.VARCHAR else ""
        print(f"    - {field.name} ({field_type}{extra})")


def main():
    """Main execution function."""
    print("=" * 60)
    print("Busibox Milvus Initialization Script")
    print("=" * 60)
    print()
    
    # Get configuration
    config = get_config()
    
    print(f"Configuration:")
    print(f"  Host: {config['host']}")
    print(f"  Port: {config['port']}")
    print(f"  Embedding Dimension: {config['embedding_dim']}")
    print(f"  Collection Name: {config['collection_name']}")
    print()
    
    # Connect to Milvus
    if not connect_milvus(config["host"], config["port"]):
        sys.exit(1)
    
    print()
    
    # Create collection
    collection = create_collection(
        config["collection_name"],
        config["embedding_dim"]
    )
    
    print()
    
    # Create index
    create_index(collection)
    
    print()
    
    # Load collection
    load_collection(collection)
    
    # Verify setup
    verify_setup(collection)
    
    print()
    print("=" * 60)
    print("✓ Milvus initialization complete!")
    print("=" * 60)
    
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

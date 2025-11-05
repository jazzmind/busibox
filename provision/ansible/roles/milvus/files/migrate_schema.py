#!/usr/bin/env python3
"""
Milvus Schema Migration Script

This script migrates the Milvus collection schema from the old format to the new hybrid search format.
It will:
1. Check if 'document_embeddings' exists with old schema
2. Drop it if schema is incompatible
3. Create 'documents' collection with hybrid search schema

Usage:
    python migrate_schema.py

Environment Variables:
    MILVUS_HOST: Milvus server host (default: localhost)
    MILVUS_PORT: Milvus server port (default: 19530)
"""

import os
import sys
from pymilvus import (
    connections,
    utility,
    Collection,
)


def get_config():
    """Get configuration from environment variables with defaults."""
    return {
        "host": os.getenv("MILVUS_HOST", "localhost"),
        "port": int(os.getenv("MILVUS_PORT", "19530")),
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


def check_and_migrate():
    """Check for old schema and migrate if necessary."""
    
    # Check for old 'document_embeddings' collection
    old_collection_name = "document_embeddings"
    new_collection_name = "documents"
    
    old_exists = utility.has_collection(old_collection_name)
    new_exists = utility.has_collection(new_collection_name)
    
    print(f"\nCollection Status:")
    print(f"  - '{old_collection_name}': {'EXISTS' if old_exists else 'NOT FOUND'}")
    print(f"  - '{new_collection_name}': {'EXISTS' if new_exists else 'NOT FOUND'}")
    
    # If new collection already exists, check its schema
    if new_exists:
        print(f"\n'{new_collection_name}' collection already exists")
        collection = Collection(new_collection_name)
        field_names = [f.name for f in collection.schema.fields]
        required_fields = ["id", "file_id", "text", "text_dense", "text_sparse", "modality"]
        missing_fields = [f for f in required_fields if f not in field_names]
        
        if missing_fields:
            print(f"  ⚠ WARNING: Missing required fields: {missing_fields}")
            print(f"  Current fields: {field_names}")
            print(f"  The schema needs to be recreated.")
            return False
        else:
            print(f"  ✓ Schema is correct with {len(field_names)} fields")
            print(f"  ✓ Contains {collection.num_entities} entities")
            return True
    
    # If old collection exists with wrong schema, drop it
    if old_exists:
        collection = Collection(old_collection_name)
        field_names = [f.name for f in collection.schema.fields]
        
        # Check if it has the new hybrid schema
        required_fields = ["id", "file_id", "text", "text_dense", "text_sparse", "modality"]
        missing_fields = [f for f in required_fields if f not in field_names]
        
        if missing_fields:
            print(f"\n'{old_collection_name}' has OLD schema (fields: {field_names})")
            print(f"  Missing required fields: {missing_fields}")
            
            entity_count = collection.num_entities
            if entity_count > 0:
                print(f"  ⚠ WARNING: Collection contains {entity_count} entities")
                print(f"  These will be LOST if we proceed with migration.")
                
                # In production, you might want to export data here
                # For now, we'll just warn
            
            print(f"\nDropping '{old_collection_name}'...")
            utility.drop_collection(old_collection_name)
            print(f"  ✓ Dropped successfully")
        else:
            print(f"\n'{old_collection_name}' has correct schema")
            print(f"  Renaming to '{new_collection_name}' would require manual data migration")
            print(f"  For now, just ensure hybrid_schema.py runs to create '{new_collection_name}'")
    
    print(f"\n✓ Migration check complete")
    print(f"  Next step: Run hybrid_schema.py to create '{new_collection_name}' collection")
    return True


def main():
    """Main execution function."""
    config = get_config()
    
    print("=" * 60)
    print("Milvus Schema Migration")
    print("=" * 60)
    
    # Connect to Milvus
    if not connect_milvus(config["host"], config["port"]):
        sys.exit(1)
    
    # Check and migrate
    try:
        success = check_and_migrate()
        
        # Disconnect
        connections.disconnect("default")
        print("\n" + "=" * 60)
        
        if success:
            print("✓ Migration completed successfully")
            sys.exit(0)
        else:
            print("✗ Migration needs attention")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        connections.disconnect("default")
        sys.exit(1)


if __name__ == "__main__":
    main()


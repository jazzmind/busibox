#!/usr/bin/env python3
"""
Milvus Partition Initialization Script

This script ensures Milvus collections are configured with partition support
for group-based access control.

Partitions:
- personal_{user_id} - Personal documents for each user
- group_{group_id} - Group-shared documents

This script is idempotent and can be run multiple times safely.
"""

import os
import sys
from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType

# Configuration from environment
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
COLLECTION_NAME = "document_embeddings"


def connect_milvus():
    """Connect to Milvus server"""
    print(f"Connecting to Milvus at {MILVUS_HOST}:{MILVUS_PORT}...")
    connections.connect(
        alias="default",
        host=MILVUS_HOST,
        port=MILVUS_PORT
    )
    print("✓ Connected to Milvus")


def check_collection_exists():
    """Check if collection exists"""
    exists = utility.has_collection(COLLECTION_NAME)
    print(f"Collection '{COLLECTION_NAME}' exists: {exists}")
    return exists


def verify_partition_support():
    """Verify collection supports partitions"""
    if not check_collection_exists():
        print(f"⚠ Collection '{COLLECTION_NAME}' does not exist yet")
        print("  This is normal for initial deployment.")
        print("  The collection will be created by the ingest service or hybrid_schema.py")
        print("  Partitions will be created automatically when documents are uploaded.")
        return None  # None = collection doesn't exist yet (not an error)
    
    collection = Collection(COLLECTION_NAME)
    
    # Check if collection has partitions
    partitions = collection.partitions
    print(f"✓ Collection has {len(partitions)} partitions:")
    for partition in partitions:
        print(f"  - {partition.name}")
    
    return True


def create_example_partitions():
    """
    Create example partitions to demonstrate the structure.
    
    In production, partitions are created on-demand when:
    1. A user uploads their first personal document
    2. A group is created and receives its first document
    """
    collection = Collection(COLLECTION_NAME)
    
    # Example personal partition
    example_personal = "personal_example"
    if not collection.has_partition(example_personal):
        collection.create_partition(example_personal)
        print(f"✓ Created example partition: {example_personal}")
    else:
        print(f"  Partition already exists: {example_personal}")
    
    # Example group partition
    example_group = "group_example"
    if not collection.has_partition(example_group):
        collection.create_partition(example_group)
        print(f"✓ Created example partition: {example_group}")
    else:
        print(f"  Partition already exists: {example_group}")
    
    print("\n✓ Partition structure verified")
    print("\nPartition Naming Convention:")
    print("  - Personal documents: personal_{user_id}")
    print("  - Group documents: group_{group_id}")
    print("\nPartitions are created automatically by the ingest worker")
    print("when documents are uploaded.")


def main():
    """Main execution"""
    try:
        connect_milvus()
        
        result = verify_partition_support()
        
        if result is None:
            # Collection doesn't exist yet - this is OK for initial deployment
            print("\n" + "="*60)
            print("⚠ Milvus partition initialization skipped (no collection)")
            print("  Partitions will be created when documents are ingested.")
            print("="*60)
            sys.exit(0)  # Exit successfully - not an error
        
        if result is False:
            print("\n✗ Partition support verification failed")
            sys.exit(1)
        
        print("\nCreating example partitions...")
        create_example_partitions()
        
        print("\n" + "="*60)
        print("✓ Milvus partition initialization complete")
        print("="*60)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        connections.disconnect("default")


if __name__ == "__main__":
    main()


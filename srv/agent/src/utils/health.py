"""
Health Check Utilities

Helper functions for checking service dependencies.
Each function returns True if the dependency is healthy, False otherwise.
"""

import os

import psycopg2
from pymilvus import connections, utility
from minio import Minio
import redis


def check_postgres() -> bool:
    """
    Check PostgreSQL connectivity.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "10.96.200.26"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "busibox"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=5,
        )
        
        # Execute simple query
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        
        cur.close()
        conn.close()
        
        return result == (1,)
    
    except Exception:
        return False


def check_milvus() -> bool:
    """
    Check Milvus connectivity.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        # Connect to Milvus
        connections.connect(
            alias="health_check",
            host=os.getenv("MILVUS_HOST", "10.96.200.27"),
            port=int(os.getenv("MILVUS_PORT", "19530")),
            timeout=5,
        )
        
        # Check if server is ready
        is_ready = utility.get_server_version(using="health_check")
        
        # Disconnect
        connections.disconnect("health_check")
        
        return is_ready is not None
    
    except Exception:
        return False


def check_minio() -> bool:
    """
    Check MinIO connectivity.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        # Create MinIO client
        client = Minio(
            endpoint=os.getenv("MINIO_ENDPOINT", "10.96.200.28:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
        
        # List buckets (lightweight operation)
        buckets = client.list_buckets()
        
        # If we get here, connection is successful
        return True
    
    except Exception:
        return False


def check_redis() -> bool:
    """
    Check Redis connectivity.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        # Create Redis client
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "10.96.200.29"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        
        # Ping server
        response = client.ping()
        
        # Close connection
        client.close()
        
        return response is True
    
    except Exception:
        return False

